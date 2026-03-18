"""
SSH Manager - handles SSH connections, tunnels, and daemon lifecycle on remote machines.

Uses asyncssh for async SSH operations and manages:
- SSH connection pool to remote machines
- Port forwarding tunnels (local:port -> remote:daemon_port)
- Remote daemon deployment and lifecycle
- Skills sync via SCP
- Localhost mode for running daemon on the head node itself
"""

import asyncio
import logging
import os
import platform
import shutil
import socket
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
        conn: Optional[asyncssh.SSHClientConnection],
        listener: Optional[asyncssh.SSHListener],
        is_localhost: bool = False,
    ):
        self.machine_id = machine_id
        self.local_port = local_port
        self.conn = conn
        self.listener = listener
        self.is_localhost = is_localhost

    @property
    def alive(self) -> bool:
        """Check if the tunnel connection is still alive."""
        if self.is_localhost:
            return True  # Localhost tunnels are always "alive"
        try:
            return self.conn is not None and not self.conn.is_closed()  # type: ignore[union-attr]
        except Exception:
            return False

    async def close(self) -> None:
        """Close the tunnel."""
        if self.is_localhost:
            return  # Nothing to close for localhost
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
        self._rust_binary = self._resolve_daemon_binary()

    @staticmethod
    def _resolve_daemon_binary() -> Path:
        """Resolve the daemon binary path.

        Resolution order:
        1. target/release/codecast-daemon (dev: local cargo build)
        2. On PATH (installed via pip install codecast with setuptools-rust)
        3. head/bin/codecast-daemon-{platform} (CI-bundled wheel)
        4. Falls back to dev path (cargo build will be triggered on deploy)
        """
        project_root = Path(__file__).parent.parent

        # 1. Dev build
        dev_binary = project_root / "target" / "release" / "codecast-daemon"
        if dev_binary.exists():
            return dev_binary

        # 2. On PATH (installed via pip install codecast)
        which = shutil.which("codecast-daemon")
        if which:
            return Path(which)

        # 3. Bundled binary matching current platform
        bundled = SSHManager._get_bundled_binary_path()
        if bundled and bundled.exists():
            return bundled

        # 4. Fallback to dev path (will trigger cargo build on deploy)
        return dev_binary

    @staticmethod
    def _get_bundled_binary_path() -> Optional[Path]:
        """Get path to bundled daemon binary for current platform."""
        system = platform.system().lower()
        machine = platform.machine().lower()

        platform_map = {
            ("linux", "x86_64"): "codecast-daemon-linux-x64",
            ("linux", "amd64"): "codecast-daemon-linux-x64",
            ("darwin", "arm64"): "codecast-daemon-macos-arm64",
            ("darwin", "aarch64"): "codecast-daemon-macos-arm64",
            ("windows", "x86_64"): "codecast-daemon-windows-x64.exe",
            ("windows", "amd64"): "codecast-daemon-windows-x64.exe",
        }

        binary_name = platform_map.get((system, machine))
        if not binary_name:
            return None

        return Path(__file__).parent / "bin" / binary_name

    def _alloc_port(self) -> int:
        """Allocate an available local port for SSH tunnel."""
        while self._next_port < 19100 + 200:
            port = self._next_port
            self._next_port += 1
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", port))
                s.close()
                return port
            except OSError:
                logger.debug(f"Port {port} in use, trying next")
        raise RuntimeError("No available local port in range 19100..19300")

    async def _read_daemon_port_remote(self, conn: asyncssh.SSHClientConnection, default: int) -> int:
        """Read the actual daemon port from ~/.codecast/daemon.port on a remote machine."""
        try:
            result = await conn.run("cat ~/.codecast/daemon.port 2>/dev/null || true")
            stdout = (result.stdout or "").strip()
            if stdout:
                return int(stdout)
        except (ValueError, Exception) as e:
            logger.debug(f"Could not read remote daemon.port: {e}")
        return default

    def _read_daemon_port_local(self, default: int) -> int:
        """Read the actual daemon port from ~/.codecast/daemon.port locally."""
        port_file = Path.home() / ".codecast" / "daemon.port"
        try:
            if port_file.exists():
                return int(port_file.read_text().strip())
        except (ValueError, Exception) as e:
            logger.debug(f"Could not read local daemon.port: {e}")
        return default

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

    async def _connect_ssh(self, machine: MachineConfig, timeout: float = 30.0) -> asyncssh.SSHClientConnection:
        """Establish SSH connection to a machine with timeout."""
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
            logger.info(f"Connecting to jump host {machine.proxy_jump} ({jump_machine.host})...")
            jump_conn = await asyncio.wait_for(
                asyncssh.connect(**jump_kwargs), timeout=timeout
            )
            connect_kwargs["tunnel"] = jump_conn

        logger.info(f"Connecting to {machine.id} ({machine.host}:{machine.port})...")
        conn = await asyncio.wait_for(
            asyncssh.connect(**connect_kwargs), timeout=timeout
        )
        return conn

    async def ensure_tunnel(self, machine_id: str) -> int:
        """
        Ensure an SSH tunnel exists to the remote machine's daemon port.
        Returns the local port number for accessing the daemon.

        For localhost machines, no SSH tunnel is created; the daemon port
        is returned directly.
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

        # Localhost mode: no SSH, just ensure daemon is running locally
        if machine.localhost:
            logger.info(f"Localhost machine {machine_id}: using direct connection to port {machine.daemon_port}")
            await self._ensure_daemon_local(machine)
            actual_port = self._read_daemon_port_local(machine.daemon_port)
            if actual_port != machine.daemon_port:
                logger.info(f"Daemon on {machine_id} using port {actual_port} (configured: {machine.daemon_port})")
            tunnel = SSHTunnel(
                machine_id,
                actual_port,
                conn=None,
                listener=None,
                is_localhost=True,
            )
            self.tunnels[machine_id] = tunnel
            return actual_port

        local_port = self._alloc_port()

        # Establish SSH connection
        conn = await self._connect_ssh(machine)

        # Ensure daemon is running on remote (may pick a different port)
        await self._ensure_daemon(machine_id, conn)

        # Read the actual daemon port (may differ from configured if port was busy)
        actual_remote_port = await self._read_daemon_port_remote(conn, machine.daemon_port)
        if actual_remote_port != machine.daemon_port:
            logger.info(f"Daemon on {machine_id} using port {actual_remote_port} (configured: {machine.daemon_port})")

        logger.info(f"Creating SSH tunnel: localhost:{local_port} -> {machine_id}:localhost:{actual_remote_port}")

        # Create local port forwarding to actual daemon port
        listener = await conn.forward_local_port(
            "127.0.0.1",
            local_port,
            "127.0.0.1",
            actual_remote_port,
        )

        tunnel = SSHTunnel(machine_id, local_port, conn, listener)
        self.tunnels[machine_id] = tunnel

        logger.info(f"Tunnel to {machine_id} ready on port {local_port}")
        return local_port

    async def _ensure_daemon(self, machine_id: str, conn: asyncssh.SSHClientConnection) -> None:
        """Ensure the daemon process is running on the remote machine."""
        machine = self._get_machine(machine_id)
        install_dir = self.config.daemon.install_dir

        # Check if daemon is already running
        result = await conn.run(f"pgrep -f 'codecast-daemon' || true")
        stdout = result.stdout.strip() if result.stdout else ""

        if stdout:
            # Verify the running daemon is actually responsive
            check_port = await self._read_daemon_port_remote(conn, machine.daemon_port)
            health_result = await conn.run(
                f"curl -sf http://127.0.0.1:{check_port}/rpc "
                f'-d \'{{"method":"health.check"}}\' '
                f"-H 'Content-Type: application/json' 2>/dev/null || true"
            )
            health_out = health_result.stdout.strip() if health_result.stdout else ""
            if '"ok":true' in health_out or '"ok": true' in health_out:
                logger.info(f"Daemon already running on {machine_id} (PID: {stdout})")
                return

            # Daemon process exists but is unresponsive — kill and restart
            logger.warning(f"Daemon on {machine_id} (PID: {stdout}) is unresponsive, restarting...")
            await conn.run(f"kill -9 {stdout.splitlines()[0]} 2>/dev/null || true")
            await asyncio.sleep(1)

        logger.info(f"Daemon not running on {machine_id}, starting...")

        # Check if daemon binary exists on remote — at install_dir or on PATH
        binary_path = f"{install_dir}/codecast-daemon"
        check_result = await conn.run(
            f"test -x {binary_path} && echo 'exists' "
            f"|| command -v codecast-daemon >/dev/null 2>&1 && echo 'on-path' "
            f"|| echo 'missing'"
        )
        check_out = check_result.stdout.strip() if check_result.stdout else ""

        if check_out == "on-path":
            # Daemon installed via pip on remote — resolve its path
            path_result = await conn.run("command -v codecast-daemon")
            binary_path = (path_result.stdout or "").strip()
            logger.info(f"Using pip-installed daemon on {machine_id}: {binary_path}")
        elif check_out == "missing":
            if self.config.daemon.auto_deploy:
                await self._deploy_daemon(machine_id, conn)
            else:
                raise RuntimeError(
                    f"Daemon not installed on {machine_id} at {install_dir}. "
                    "Set daemon.auto_deploy: true or install manually."
                )

        log_file = self.config.daemon.log_file

        # Build PATH that includes ~/.local/bin (for claude CLI)
        extra_paths = [f"/home/{machine.user}/.local/bin"]
        path_value = ":".join(extra_paths) + ":$PATH"

        # Start daemon with enriched PATH
        start_cmd = f"DAEMON_PORT={machine.daemon_port} PATH={path_value} nohup {binary_path} > {log_file} 2>&1 &"
        await conn.run(start_cmd)

        # Wait for daemon to be ready (poll health endpoint)
        # Read actual port from port file since daemon may have picked a different one
        for attempt in range(15):
            await asyncio.sleep(2)
            check_port = await self._read_daemon_port_remote(conn, machine.daemon_port)
            health_result = await conn.run(
                f"curl -sf http://127.0.0.1:{check_port}/rpc "
                f'-d \'{{"method":"health.check"}}\' '
                f"-H 'Content-Type: application/json' 2>/dev/null || true"
            )
            health_out = health_result.stdout.strip() if health_result.stdout else ""
            if '"ok":true' in health_out or '"ok": true' in health_out:
                logger.info(f"Daemon started on {machine_id} (port {check_port})")
                return

        raise RuntimeError(f"Daemon failed to start on {machine_id} after 30s")

    async def _ensure_daemon_local(self, machine: MachineConfig) -> None:
        """
        Ensure the daemon is running locally (for localhost machines).
        Spawns it as a local subprocess instead of via SSH.
        """
        install_dir = Path(self.config.daemon.install_dir).expanduser()

        # Check if daemon is already running
        try:
            result = subprocess.run(["pgrep", "-f", "codecast-daemon"], capture_output=True, text=True)
            if result.stdout.strip():
                logger.info(f"Local daemon already running (PID: {result.stdout.strip()})")
                return
        except FileNotFoundError:
            pass  # pgrep not available, continue with startup

        logger.info(f"Local daemon not running, starting on port {machine.daemon_port}...")

        # Check if daemon binary exists — at install_dir or on PATH
        binary_path = install_dir / "codecast-daemon"
        if not binary_path.exists():
            which = shutil.which("codecast-daemon")
            if which:
                binary_path = Path(which)
                logger.info(f"Using pip-installed daemon: {binary_path}")
            elif self.config.daemon.auto_deploy:
                await self._deploy_daemon_local(machine, install_dir)
            else:
                raise RuntimeError(
                    f"Daemon not installed at {install_dir}. Set daemon.auto_deploy: true or install manually."
                )

        log_file = Path(self.config.daemon.log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["DAEMON_PORT"] = str(machine.daemon_port)
        home_local_bin = Path.home() / ".local" / "bin"
        if home_local_bin.exists():
            env["PATH"] = str(home_local_bin) + ":" + env.get("PATH", "")

        # Start daemon as background process
        with open(log_file, "a") as lf:
            subprocess.Popen(
                [str(binary_path)],
                cwd=str(install_dir),
                env=env,
                stdout=lf,
                stderr=lf,
                start_new_session=True,  # Detach from parent process
            )

        # Wait for daemon to be ready
        # Read actual port from port file since daemon may have picked a different one
        import aiohttp

        for attempt in range(15):
            await asyncio.sleep(2)
            check_port = self._read_daemon_port_local(machine.daemon_port)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://127.0.0.1:{check_port}/rpc",
                        json={"method": "health.check"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        data = await resp.json()
                        result = data.get("result", data)
                        if result.get("ok"):
                            logger.info(f"Local daemon started on port {check_port}")
                            return
            except Exception:
                pass

        raise RuntimeError(f"Local daemon failed to start after 30s")

    async def _deploy_daemon_local(self, machine: MachineConfig, install_dir: Path) -> None:
        """Deploy daemon binary locally (for localhost machines)."""
        logger.info(f"Deploying daemon locally to {install_dir}")

        # Build Rust daemon if needed
        if not self._rust_binary.exists():
            logger.info("Building Rust daemon locally...")
            project_root = Path(__file__).parent.parent
            result = subprocess.run(
                ["cargo", "build", "--release"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Daemon build failed: {result.stderr}")

        # Create install directory and copy binary
        install_dir.mkdir(parents=True, exist_ok=True)
        dest_binary = install_dir / "codecast-daemon"
        shutil.copy2(str(self._rust_binary), str(dest_binary))
        dest_binary.chmod(0o755)

        logger.info(f"Daemon deployed locally to {install_dir}")

    async def _deploy_daemon(self, machine_id: str, conn: asyncssh.SSHClientConnection) -> None:
        """Deploy daemon binary to remote machine via SCP."""
        install_dir = self.config.daemon.install_dir

        logger.info(f"Deploying daemon to {machine_id}:{install_dir}")

        # Build Rust daemon locally first if needed
        if not self._rust_binary.exists():
            logger.info("Building Rust daemon locally...")
            project_root = Path(__file__).parent.parent
            result = subprocess.run(
                ["cargo", "build", "--release"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Daemon build failed: {result.stderr}")

        # Create remote directory
        await conn.run(f"mkdir -p {install_dir}")

        # SCP binary to remote
        remote_binary = f"{install_dir}/codecast-daemon"
        await asyncssh.scp(str(self._rust_binary), (conn, remote_binary))
        await conn.run(f"chmod +x {remote_binary}")

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

        # Localhost: use local file copy
        if machine.localhost:
            try:
                target = Path(remote_path)
                claude_md_src = skills_dir / "CLAUDE.md"
                if claude_md_src.exists() and not (target / "CLAUDE.md").exists():
                    shutil.copy2(str(claude_md_src), str(target / "CLAUDE.md"))
                    logger.info(f"Synced CLAUDE.md to local:{remote_path}")

                skills_src = skills_dir / ".claude" / "skills"
                if skills_src.exists():
                    dest_skills = target / ".claude" / "skills"
                    dest_skills.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(str(skills_src), str(dest_skills), dirs_exist_ok=True)
                    logger.info(f"Synced .claude/skills/ to local:{remote_path}")
            except Exception as e:
                logger.warning(f"Skills sync failed for local:{remote_path}: {e}")
            return

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

        Checks all machines in parallel with a short timeout.
        Skips machines that are only used as jump hosts (proxy_jump targets).
        """
        # Find which machines are only used as jump hosts
        jump_hosts = {m.proxy_jump for m in self.machines.values() if m.proxy_jump}

        # Filter to machines we want to check
        targets = [(mid, m) for mid, m in self.machines.items() if not (mid in jump_hosts and not m.default_paths)]

        async def check_machine(machine_id: str, machine: MachineConfig) -> dict:
            status = "unknown"
            daemon_status = "unknown"

            if machine.localhost:
                status = "online"
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", "codecast-daemon"],
                        capture_output=True,
                        text=True,
                    )
                    daemon_status = "running" if result.stdout.strip() else "stopped"
                except FileNotFoundError:
                    daemon_status = "unknown"
            else:
                try:
                    conn = await asyncio.wait_for(
                        self._connect_ssh(machine),
                        timeout=5.0,
                    )
                    status = "online"
                    daemon_check = await conn.run(
                        f"pgrep -f 'codecast-daemon' > /dev/null 2>&1 && echo 'running' || echo 'stopped'"
                    )
                    daemon_status = (daemon_check.stdout or "").strip()
                    conn.close()
                except (asyncio.TimeoutError, OSError, asyncssh.Error) as e:
                    status = "offline"
                    daemon_status = "unknown"
                    logger.debug(f"list_machines: {machine_id} unreachable: {e}")

            return {
                "id": machine_id,
                "host": machine.host,
                "user": machine.user,
                "status": status,
                "daemon": daemon_status if status == "online" else "unknown",
                "default_paths": machine.default_paths,
                "localhost": machine.localhost,
            }

        # Check all machines in parallel
        results = await asyncio.gather(*(check_machine(mid, m) for mid, m in targets))
        return list(results)

    def get_local_port(self, machine_id: str) -> Optional[int]:
        """Get the local tunnel port for a machine, if tunnel exists."""
        tunnel = self.tunnels.get(machine_id)
        if tunnel and tunnel.alive:
            return tunnel.local_port
        return None

    async def upload_files(
        self,
        machine_id: str,
        file_entries: list,
        remote_base: Optional[str] = None,
    ) -> dict[str, str]:
        """
        SCP files to the remote machine (or local copy for localhost).

        Args:
            machine_id: Target machine ID.
            file_entries: List of FileEntry objects to upload.
            remote_base: Remote directory (defaults to config.file_pool.remote_dir).

        Returns:
            Dict mapping file_id -> remote_path.
        """
        if not remote_base:
            remote_base = self.config.file_pool.remote_dir

        machine = self._get_machine(machine_id)

        if machine.localhost:
            # Local copy
            base = Path(remote_base)
            base.mkdir(parents=True, exist_ok=True)
            mapping: dict[str, str] = {}
            for entry in file_entries:
                remote_filename = f"{entry.file_id}_{entry.original_name}"
                remote_path = str(base / remote_filename)
                shutil.copy2(str(entry.local_path), remote_path)
                mapping[entry.file_id] = remote_path
                logger.info(f"Copied {entry.original_name} to local:{remote_path}")
            return mapping

        # Get SSH connection (reuse tunnel connection)
        if machine_id not in self.tunnels or not self.tunnels[machine_id].alive:
            raise ValueError(f"No active tunnel to {machine_id}")
        conn = self.tunnels[machine_id].conn

        # Ensure remote directory exists
        await conn.run(f"mkdir -p {remote_base}")

        mapping = {}
        for entry in file_entries:
            remote_filename = f"{entry.file_id}_{entry.original_name}"
            remote_path = f"{remote_base}/{remote_filename}"
            await asyncssh.scp(str(entry.local_path), (conn, remote_path))
            mapping[entry.file_id] = remote_path
            logger.info(f"Uploaded {entry.original_name} to {machine_id}:{remote_path}")

        return mapping

    async def download_file(
        self,
        machine_id: str,
        remote_path: str,
        local_dir: str,
    ) -> Path:
        """Download a file from a remote machine via SCP.

        Args:
            machine_id: Target machine ID.
            remote_path: Absolute path on the remote machine.
            local_dir: Local directory to download into.

        Returns:
            Path to the downloaded local file.

        Raises:
            FileNotFoundError: If the remote file doesn't exist.
            ValueError: If no active tunnel exists.
        """
        machine = self._get_machine(machine_id)
        filename = Path(remote_path).name
        local_base = Path(local_dir).expanduser()
        local_base.mkdir(parents=True, exist_ok=True)
        local_path = local_base / filename

        if machine.localhost:
            # Local copy — expand ~ in remote path
            src = Path(remote_path).expanduser()
            if not src.exists():
                raise FileNotFoundError(f"File not found: {remote_path}")
            shutil.copy2(str(src), str(local_path))
            logger.info(f"Copied local file {remote_path} to {local_path}")
            return local_path

        # Remote: use SCP via existing tunnel connection
        if machine_id not in self.tunnels or not self.tunnels[machine_id].alive:
            raise ValueError(f"No active tunnel to {machine_id}")
        conn = self.tunnels[machine_id].conn

        # Check if file exists on remote
        check = await conn.run(f"test -f {remote_path} && echo 'exists' || echo 'missing'")
        if "missing" in (check.stdout or ""):
            raise FileNotFoundError(f"Remote file not found: {remote_path}")

        await asyncssh.scp((conn, remote_path), str(local_path))
        logger.info(f"Downloaded {machine_id}:{remote_path} to {local_path}")
        return local_path

    async def close_all(self) -> None:
        """Close all SSH tunnels and connections."""
        for machine_id, tunnel in list(self.tunnels.items()):
            logger.info(f"Closing tunnel to {machine_id}")
            await tunnel.close()
        self.tunnels.clear()
