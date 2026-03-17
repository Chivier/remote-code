"""SSH Transport - connects to a remote daemon via SSH tunnel."""

import logging
import socket
from typing import Optional

import asyncssh

from . import Transport

logger = logging.getLogger(__name__)


class SSHTransport(Transport):
    """Transport that creates an SSH tunnel to a remote daemon.

    Establishes an SSH connection and sets up local port forwarding
    so that requests to http://127.0.0.1:{local_port} are tunneled
    to the remote daemon on 127.0.0.1:{daemon_port}.
    """

    def __init__(
        self,
        peer_id: str,
        ssh_host: str,
        ssh_user: str,
        daemon_port: int = 9100,
        ssh_port: int = 22,
        ssh_key: Optional[str] = None,
        proxy_jump: Optional[str] = None,
        proxy_command: Optional[str] = None,
        password: Optional[str] = None,
        local_port: Optional[int] = None,
        peer_configs: Optional[dict] = None,
    ):
        self._peer_id = peer_id
        self._ssh_host = ssh_host
        self._ssh_user = ssh_user
        self._daemon_port = daemon_port
        self._ssh_port = ssh_port
        self._ssh_key = ssh_key
        self._proxy_jump = proxy_jump
        self._proxy_command = proxy_command
        self._password = password
        self._local_port = local_port or self._alloc_port()
        self._peer_configs = peer_configs or {}
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._listener: Optional[asyncssh.SSHListener] = None

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def get_base_url(self) -> str:
        return f"http://127.0.0.1:{self._local_port}"

    def get_auth_headers(self) -> dict[str, str]:
        return {}  # SSH tunnel provides authentication

    def is_alive(self) -> bool:
        try:
            return self._conn is not None and not self._conn.is_closed()
        except Exception:
            return False

    @property
    def connection(self) -> Optional[asyncssh.SSHClientConnection]:
        """Expose SSH connection for file uploads and remote commands."""
        return self._conn

    async def connect(self) -> None:
        """Establish SSH connection and set up local port forwarding."""
        connect_kwargs: dict = {
            "host": self._ssh_host,
            "port": self._ssh_port,
            "username": self._ssh_user,
            "known_hosts": None,
        }

        if self._ssh_key:
            connect_kwargs["client_keys"] = [self._ssh_key]

        if self._password:
            connect_kwargs["password"] = self._password

        # Handle proxy jump: connect to jump host first, then tunnel through it
        if self._proxy_jump and self._proxy_jump in self._peer_configs:
            jump_cfg = self._peer_configs[self._proxy_jump]
            jump_kwargs: dict = {
                "host": jump_cfg["ssh_host"],
                "port": jump_cfg.get("ssh_port", 22),
                "username": jump_cfg["ssh_user"],
                "known_hosts": None,
            }
            if jump_cfg.get("ssh_key"):
                jump_kwargs["client_keys"] = [jump_cfg["ssh_key"]]
            if jump_cfg.get("password"):
                jump_kwargs["password"] = jump_cfg["password"]

            jump_conn = await asyncssh.connect(**jump_kwargs)
            connect_kwargs["tunnel"] = jump_conn

        self._conn = await asyncssh.connect(**connect_kwargs)

        # Set up local port forwarding
        self._listener = await self._conn.forward_local_port(
            "127.0.0.1",
            self._local_port,
            "127.0.0.1",
            self._daemon_port,
        )

        logger.info(
            f"SSH tunnel to {self._peer_id}: "
            f"localhost:{self._local_port} -> {self._ssh_host}:{self._daemon_port}"
        )

    async def close(self) -> None:
        """Close the SSH tunnel and connection."""
        try:
            if self._listener:
                self._listener.close()
            if self._conn and not self._conn.is_closed():
                self._conn.close()
                await self._conn.wait_closed()
        except Exception as e:
            logger.warning(f"Error closing SSH transport to {self._peer_id}: {e}")
        finally:
            self._conn = None
            self._listener = None

    @staticmethod
    def _alloc_port() -> int:
        """Allocate an available local port by binding to port 0."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
        finally:
            s.close()
