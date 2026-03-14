"""
SSH Manager - handles SSH connections, tunnels, and daemon lifecycle on remote machines.

Uses asyncssh for async SSH operations and manages:
- SSH connection pool to remote machines
- Port forwarding tunnels (local:port -> remote:daemon_port)
- Remote daemon deployment and lifecycle
- Skills sync via SCP
"""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import asyncssh

from .config import Config, MachineConfig

logger = logging.getLogger(__name__)


class SSHTunnel:
    """Represents an active SSH tunnel to a remote machine."""

    def __init__(
        self,
        machine_id: str,
        local_port: int,
        conn: asyncssh.SSHClientConnection,
        listener: asyncssh.SSHListener,
    ):
        self.machine_id = machine_id
        self.local_port = local_port
        self.conn = conn
        self.listener = listener

    @property
    def alive(self) -> bool:
        """Check if the tunnel connection is still alive."""
        try:
            return self.conn is not None and not self.conn.is_closed()  # type: ignore[union-attr]
        except Exception:
            return False

    async def close(self) -> None:
        """Close the tunnel."""
        try:
            if self.listener:
                self.listener.close()
            if self.conn and not self.conn.is_closed():  # type: ignore[union-attr]
                self.conn.close()
                await self.conn.wait_closed()
        except Exception as e:
            logger.warning(f"Error closing tunnel to {self.machine_id}: {e}")


class SSHManager:
    """Manages SSH connections, tunnels, and remote daemon lifecycle."""

    def __init__(self, config: Config):
        self.config = config
        self.machines = config.machines
        self.tunnels: dict[str, SSHTunnel] = {}
        self._next_port = 19100  # Starting port for local tunnel endpoints
        self._daemon_source = Path(__file__).parent.parent / "daemon"

    def _alloc_port(self) -> int:
        """Allocate a local port for SSH tunnel."""
        port = self._next_port
        self._next_port += 1
        return port

    def _get_machine(self, machine_id: str) -> MachineConfig:
        """Get machine config by ID, raising if not found."""
        if machine_id not in self.machines:
            raise ValueError(f"Unknown machine: {machine_id}. Available: {list(self.machines.keys())}")
        return self.machines[machine_id]

    def _resolve_password(self, machine: MachineConfig) -> Optional[str]:
        """Resolve password from config. Supports 'file:/path' syntax."""
        if not machine.password:
            return None
        pw = machine.password
        if pw.startswith("file:"):
            pw_path = Path(pw[5:]).expanduser()
            if pw_path.exists():
                return pw_path.read_text().strip()
            else:
                logger.warning(f"Password file not found: {pw_path}")
                return None
        return pw

    async def _connect_ssh(self, machine: MachineConfig) -> asyncssh.SSHClientConnection:
        """Establish SSH connection to a machine."""
        connect_kwargs: dict = {
            "host": machine.host,
            "port": machine.port,
            "username": machine.user,
            "known_hosts": None,  # Accept any host key (single user, trusted network)
        }

        if machine.ssh_key:
            connect_kwargs["client_keys"] = [machine.ssh_key]

        password = self._resolve_password(machine)
        if password:
            connect_kwargs["password"] = password

        if machine.proxy_jump:
            # Connect through jump host
            jump_machine = self._get_machine(machine.proxy_jump)
            jump_password = self._resolve_password(jump_machine)
            jump_kwargs: dict = {
                "host": jump_machine.host,
                "port": jump_machine.port,
                "username": jump_machine.user,
                "known_hosts": None,
            }
            if jump_machine.ssh_key:
                jump_kwargs["client_keys"] = [jump_machine.ssh_key]
            if jump_password:
                jump_kwargs["password"] = jump_password
            jump_conn = await asyncssh.connect(**jump_kwargs)
            connect_kwargs["tunnel"] = jump_conn

        conn = await asyncssh.connect(**connect_kwargs)
        return conn

    async def ensure_tunnel(self, machine_id: str) -> int:
        """
        Ensure an SSH tunnel exists to the remote machine's daemon port.
        Returns the local port number for accessing the daemon.
        """
        # Check existing tunnel
        if machine_id in self.tunnels:
            tunnel = self.tunnels[machine_id]
            if tunnel.alive:
                logger.debug(f"Tunnel to {machine_id} already active on port {tunnel.local_port}")
                return tunnel.local_port
            else:
                logger.info(f"Tunnel to {machine_id} is dead, recreating...")
                await tunnel.close()
                del self.tunnels[machine_id]

        machine = self._get_machine(machine_id)
        local_port = self._alloc_port()

        logger.info(f"Creating SSH tunnel: localhost:{local_port} -> {machine_id}:localhost:{machine.daemon_port}")

        # Establish SSH connection
        conn = await self._connect_ssh(machine)

        # Create local port forwarding
        listener = await conn.forward_local_port(
            "127.0.0.1", local_port,
            "127.0.0.1", machine.daemon_port,
        )

        tunnel = SSHTunnel(machine_id, local_port, conn, listener)
        self.tunnels[machine_id] = tunnel

        # Ensure daemon is running on remote
        await self._ensure_daemon(machine_id, conn)

        logger.info(f"Tunnel to {machine_id} ready on port {local_port}")
        return local_port

    async def _ensure_daemon(self, machine_id: str, conn: asyncssh.SSHClientConnection) -> None:
        """Ensure the daemon process is running on the remote machine."""
        machine = self._get_machine(machine_id)
        install_dir = self.config.daemon.install_dir

        # Check if daemon is already running
        # The process is: node dist/server.js (in install_dir)
        result = await conn.run(f"pgrep -f 'node.*dist/server\\.js' || true")
        stdout = result.stdout.strip() if result.stdout else ""

        if stdout:
            logger.info(f"Daemon already running on {machine_id} (PID: {stdout})")
            return

        logger.info(f"Daemon not running on {machine_id}, starting...")

        # Check if daemon code exists on remote (both dist and node_modules)
        check_result = await conn.run(
            f"test -f {install_dir}/dist/server.js -a -d {install_dir}/node_modules && echo 'exists' || echo 'missing'"
        )
        check_out = check_result.stdout.strip() if check_result.stdout else ""

        if check_out == "missing":
            if self.config.daemon.auto_deploy:
                await self._deploy_daemon(machine_id, conn)
            else:
                raise RuntimeError(
                    f"Daemon not installed on {machine_id} at {install_dir}. "
                    "Set daemon.auto_deploy: true or install manually."
                )

        # Determine node path and build PATH with common binary locations
        node_cmd = machine.node_path or "node"
        log_file = self.config.daemon.log_file

        # Build a PATH that includes node bin dir, ~/.local/bin (for claude CLI), etc.
        # This PATH is inherited by the daemon process and all its child processes
        # (including claude CLI spawned by session-pool.ts)
        extra_paths = []
        if machine.node_path:
            from pathlib import PurePosixPath
            extra_paths.append(str(PurePosixPath(machine.node_path).parent))
        extra_paths.append(f"/home/{machine.user}/.local/bin")
        path_value = ":".join(extra_paths) + ":$PATH"

        # Start daemon with enriched PATH
        start_cmd = (
            f"cd {install_dir} && "
            f"DAEMON_PORT={machine.daemon_port} "
            f"PATH={path_value} "
            f"nohup {node_cmd} dist/server.js > {log_file} 2>&1 &"
        )
        await conn.run(start_cmd)

        # Wait for daemon to be ready (poll health endpoint)
        for attempt in range(15):
            await asyncio.sleep(2)
            health_result = await conn.run(
                f"curl -sf http://127.0.0.1:{machine.daemon_port}/rpc "
                f'-d \'{{"method":"health.check"}}\' '
                f"-H 'Content-Type: application/json' 2>/dev/null || true"
            )
            health_out = health_result.stdout.strip() if health_result.stdout else ""
            if '"ok":true' in health_out or '"ok": true' in health_out:
                logger.info(f"Daemon started on {machine_id}")
                return

        raise RuntimeError(f"Daemon failed to start on {machine_id} after 30s")

    async def _deploy_daemon(self, machine_id: str, conn: asyncssh.SSHClientConnection) -> None:
        """Deploy daemon code to remote machine via SCP."""
        install_dir = self.config.daemon.install_dir
        machine = self._get_machine(machine_id)

        logger.info(f"Deploying daemon to {machine_id}:{install_dir}")

        # Build daemon locally first if needed
        dist_dir = self._daemon_source / "dist"
        if not dist_dir.exists():
            logger.info("Building daemon locally...")
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(self._daemon_source),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Daemon build failed: {result.stderr}")

        # Create remote directory
        await conn.run(f"mkdir -p {install_dir}")

        # SCP files to remote
        # We use asyncssh.scp for file transfer
        scp_files = [
            (self._daemon_source / "package.json", f"{install_dir}/package.json"),
            (self._daemon_source / "package-lock.json", f"{install_dir}/package-lock.json"),
        ]

        for local_path, remote_path in scp_files:
            if local_path.exists():
                await asyncssh.scp(str(local_path), (conn, remote_path))

        # SCP dist directory
        await asyncssh.scp(str(dist_dir), (conn, f"{install_dir}/dist"), recurse=True)

        # Install dependencies on remote
        node_cmd = machine.node_path or "node"
        # Derive npm path from node path: replace the last path component only
        if machine.node_path:
            from pathlib import PurePosixPath
            node_bin_dir = str(PurePosixPath(machine.node_path).parent)
            npm_cmd = f"{node_bin_dir}/npm"
        else:
            node_bin_dir = None
            npm_cmd = "npm"

        # Prepend node bin dir to PATH so npm can find node
        path_prefix = f"export PATH={node_bin_dir}:$PATH && " if node_bin_dir else ""

        install_result = await conn.run(
            f"{path_prefix}cd {install_dir} && {npm_cmd} install --production 2>&1"
        )
        if install_result.exit_status != 0:
            stderr = install_result.stderr or install_result.stdout or ""
            raise RuntimeError(f"npm install failed on {machine_id}: {stderr}")

        logger.info(f"Daemon deployed to {machine_id}")

    async def sync_skills(self, machine_id: str, remote_path: str) -> None:
        """Sync skills directory to a remote project path."""
        if not self.config.skills.sync_on_start:
            return

        skills_dir = Path(self.config.skills.shared_dir)
        if not skills_dir.exists():
            logger.debug("No skills directory to sync")
            return

        machine = self._get_machine(machine_id)

        # Get or create SSH connection
        if machine_id in self.tunnels and self.tunnels[machine_id].alive:
            conn = self.tunnels[machine_id].conn
        else:
            conn = await self._connect_ssh(machine)

        # SCP skills to remote
        try:
            claude_md = skills_dir / "CLAUDE.md"
            if claude_md.exists():
                # Check if target already has CLAUDE.md
                check = await conn.run(f"test -f {remote_path}/CLAUDE.md && echo 'exists' || echo 'missing'")
                if "missing" in (check.stdout or ""):
                    await asyncssh.scp(str(claude_md), (conn, f"{remote_path}/CLAUDE.md"))
                    logger.info(f"Synced CLAUDE.md to {machine_id}:{remote_path}")

            claude_skills = skills_dir / ".claude" / "skills"
            if claude_skills.exists():
                await conn.run(f"mkdir -p {remote_path}/.claude/skills")
                await asyncssh.scp(
                    str(claude_skills),
                    (conn, f"{remote_path}/.claude/skills"),
                    recurse=True,
                )
                logger.info(f"Synced .claude/skills/ to {machine_id}:{remote_path}")

        except Exception as e:
            logger.warning(f"Skills sync failed for {machine_id}:{remote_path}: {e}")

    async def list_machines(self) -> list[dict]:
        """List all configured machines with their online status.
        
        Skips machines that are only used as jump hosts (proxy_jump targets).
        """
        # Find which machines are only used as jump hosts
        jump_hosts = {m.proxy_jump for m in self.machines.values() if m.proxy_jump}

        results = []
        for machine_id, machine in self.machines.items():
            # Skip pure jump hosts
            if machine_id in jump_hosts and not machine.default_paths:
                continue

            status = "unknown"
            daemon_status = "unknown"
            try:
                conn = await asyncio.wait_for(
                    self._connect_ssh(machine),
                    timeout=15.0,
                )
                status = "online"
                # Check if daemon is running
                daemon_check = await conn.run(
                    f"pgrep -f 'node.*dist/server\\.js' > /dev/null 2>&1 && echo 'running' || echo 'stopped'"
                )
                daemon_status = (daemon_check.stdout or "").strip()
                conn.close()
            except (asyncio.TimeoutError, OSError, asyncssh.Error) as e:
                status = "offline"
                daemon_status = "unknown"
                logger.debug(f"list_machines: {machine_id} unreachable: {e}")

            results.append({
                "id": machine_id,
                "host": machine.host,
                "user": machine.user,
                "status": status,
                "daemon": daemon_status if status == "online" else "unknown",
                "default_paths": machine.default_paths,
            })

        return results

    def get_local_port(self, machine_id: str) -> Optional[int]:
        """Get the local tunnel port for a machine, if tunnel exists."""
        tunnel = self.tunnels.get(machine_id)
        if tunnel and tunnel.alive:
            return tunnel.local_port
        return None

    async def close_all(self) -> None:
        """Close all SSH tunnels and connections."""
        for machine_id, tunnel in list(self.tunnels.items()):
            logger.info(f"Closing tunnel to {machine_id}")
            await tunnel.close()
        self.tunnels.clear()
