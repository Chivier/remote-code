"""Central peer registry – creates transports, monitors health, dispatches connections."""

import logging
import platform as _platform
import shutil
from pathlib import Path
from typing import Optional

from .config_v2 import PeerConfig
from .transport import Transport
from .transport.http import HTTPTransport
from .transport.ssh import SSHTransport

logger = logging.getLogger(__name__)


# ─── Daemon binary resolution ───


def resolve_daemon_binary() -> Path:
    """Resolve daemon binary path (extracted from SSHManager for reuse)."""
    project_root = Path(__file__).parent.parent
    dev_binary = project_root / "target" / "release" / "codecast-daemon"
    if dev_binary.exists():
        return dev_binary

    which = shutil.which("codecast-daemon")
    if which:
        return Path(which)

    # Check bundled binaries
    system = _platform.system().lower()
    machine = _platform.machine().lower()
    platform_map = {
        ("linux", "x86_64"): "codecast-daemon-linux-x64",
        ("darwin", "arm64"): "codecast-daemon-macos-arm64",
    }
    binary_name = platform_map.get((system, machine))
    if binary_name:
        bundled = Path(__file__).parent / "bin" / binary_name
        if bundled.exists():
            return bundled

    return dev_binary


# ─── PeerManager ───


class PeerManager:
    """Registry of peers with lazy transport creation and health monitoring."""

    def __init__(self) -> None:
        self.peers: dict[str, PeerConfig] = {}
        self._transports: dict[str, Transport] = {}

    # ── Registration ──

    def register(self, peer: PeerConfig) -> None:
        """Register a peer configuration."""
        self.peers[peer.id] = peer
        logger.info(f"Registered peer '{peer.id}' (transport={peer.transport})")

    def remove(self, peer_id: str) -> None:
        """Remove a peer and its cached transport."""
        if peer_id not in self.peers:
            raise KeyError(f"Peer '{peer_id}' not found")
        del self.peers[peer_id]
        self._transports.pop(peer_id, None)
        logger.info(f"Removed peer '{peer_id}'")

    def list_peers(self) -> list[dict]:
        """Return a summary list of all registered peers."""
        return [
            {
                "id": peer.id,
                "transport": peer.transport,
                "connected": self._transports.get(peer.id, None) is not None and self._transports[peer.id].is_alive(),
            }
            for peer in self.peers.values()
        ]

    # ── Transport ──

    def get_transport(self, peer_id: str) -> Transport:
        """Get (or lazily create) the transport for a peer."""
        if peer_id not in self.peers:
            raise KeyError(f"Peer '{peer_id}' not found")

        if peer_id not in self._transports:
            self._transports[peer_id] = self._create_transport(self.peers[peer_id])

        return self._transports[peer_id]

    async def ensure_connected(self, peer_id: str) -> Transport:
        """Get transport and ensure it is connected."""
        transport = self.get_transport(peer_id)
        if not transport.is_alive():
            await transport.connect()
        return transport

    # ── Health ──

    async def check_health(self, peer_id: str) -> dict:
        """Check health of a single peer."""
        if peer_id not in self.peers:
            raise KeyError(f"Peer '{peer_id}' not found")

        transport = self._transports.get(peer_id)
        return {
            "id": peer_id,
            "transport": self.peers[peer_id].transport,
            "connected": transport is not None and transport.is_alive(),
        }

    async def check_all_health(self) -> list[dict]:
        """Check health of all registered peers."""
        results = []
        for peer_id in self.peers:
            results.append(await self.check_health(peer_id))
        return results

    # ── Lifecycle ──

    async def close_all(self) -> None:
        """Close all active transports."""
        for peer_id, transport in list(self._transports.items()):
            try:
                await transport.close()
            except Exception as e:
                logger.warning(f"Error closing transport for '{peer_id}': {e}")
        self._transports.clear()

    # ── Internal ──

    def _create_transport(self, peer: PeerConfig) -> Transport:
        """Create the appropriate Transport instance for a peer."""
        if peer.transport == "http":
            return HTTPTransport(
                peer_id=peer.id,
                address=peer.address or f"{peer.ssh_host}:{peer.daemon_port}",
                token=peer.token or "",
                tls_fingerprint=peer.tls_fingerprint,
            )
        elif peer.transport == "ssh":
            # Build a dict of all peer configs for proxy jump resolution
            peer_configs: dict[str, dict] = {}
            for pid, pcfg in self.peers.items():
                if pcfg.transport == "ssh":
                    peer_configs[pid] = {
                        "ssh_host": pcfg.ssh_host,
                        "ssh_user": pcfg.ssh_user,
                        "ssh_port": pcfg.ssh_port,
                        "ssh_key": pcfg.ssh_key,
                        "password": pcfg.password,
                    }

            return SSHTransport(
                peer_id=peer.id,
                ssh_host=peer.ssh_host or "",
                ssh_user=peer.ssh_user or "",
                daemon_port=peer.daemon_port,
                ssh_port=peer.ssh_port,
                ssh_key=peer.ssh_key,
                proxy_jump=peer.proxy_jump,
                proxy_command=peer.proxy_command,
                password=peer.password,
                peer_configs=peer_configs,
            )
        elif peer.transport == "local":
            return HTTPTransport(
                peer_id=peer.id,
                address=f"127.0.0.1:{peer.daemon_port}",
                token="",
            )
        else:
            raise ValueError(f"Unknown transport type '{peer.transport}' for peer '{peer.id}'")
