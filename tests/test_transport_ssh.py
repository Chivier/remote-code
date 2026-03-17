"""Tests for SSHTransport."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from head.transport.ssh import SSHTransport


class TestSSHTransportBasic(unittest.TestCase):
    """Basic property and method tests that don't require a real SSH connection."""

    def test_get_base_url(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            daemon_port=9100,
            local_port=19100,
        )
        assert t.get_base_url() == "http://127.0.0.1:19100"

    def test_get_auth_headers_empty(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        assert t.get_auth_headers() == {}

    def test_is_alive_before_connect(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        assert t.is_alive() is False

    def test_peer_id(self):
        t = SSHTransport(
            peer_id="my-lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        assert t.peer_id == "my-lab"

    def test_default_ports(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        # Should auto-allocate a local port
        assert isinstance(t._local_port, int)
        assert t._local_port > 0

    def test_explicit_local_port(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            local_port=19200,
        )
        assert t._local_port == 19200

    def test_connection_none_before_connect(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        assert t.connection is None

    def test_is_alive_with_closed_connection(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = True
        t._conn = mock_conn
        assert t.is_alive() is False

    def test_is_alive_with_open_connection(self):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        t._conn = mock_conn
        assert t.is_alive() is True

    def test_alloc_port_returns_valid_port(self):
        port = SSHTransport._alloc_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535


class TestSSHTransportConnect(unittest.IsolatedAsyncioTestCase):
    """Tests for connect/close that mock asyncssh."""

    @patch("head.transport.ssh.asyncssh")
    async def test_connect_basic(self, mock_asyncssh):
        mock_conn = AsyncMock()
        mock_conn.is_closed = MagicMock(return_value=False)
        mock_conn.close = MagicMock()
        mock_listener = MagicMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_conn.forward_local_port = AsyncMock(return_value=mock_listener)

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            daemon_port=9100,
            local_port=19100,
        )
        await t.connect()

        mock_asyncssh.connect.assert_awaited_once()
        call_kwargs = mock_asyncssh.connect.call_args[1]
        assert call_kwargs["host"] == "10.0.1.8"
        assert call_kwargs["username"] == "testuser"
        assert call_kwargs["known_hosts"] is None

        mock_conn.forward_local_port.assert_awaited_once_with(
            "127.0.0.1", 19100, "127.0.0.1", 9100
        )
        assert t.is_alive() is True
        assert t.connection is mock_conn

    @patch("head.transport.ssh.asyncssh")
    async def test_connect_with_ssh_key(self, mock_asyncssh):
        mock_conn = AsyncMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_conn.forward_local_port = AsyncMock(return_value=MagicMock())

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            ssh_key="/home/testuser/.ssh/id_rsa",
            local_port=19100,
        )
        await t.connect()

        call_kwargs = mock_asyncssh.connect.call_args[1]
        assert call_kwargs["client_keys"] == ["/home/testuser/.ssh/id_rsa"]

    @patch("head.transport.ssh.asyncssh")
    async def test_connect_with_password(self, mock_asyncssh):
        mock_conn = AsyncMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_conn.forward_local_port = AsyncMock(return_value=MagicMock())

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            password="secret",
            local_port=19100,
        )
        await t.connect()

        call_kwargs = mock_asyncssh.connect.call_args[1]
        assert call_kwargs["password"] == "secret"

    @patch("head.transport.ssh.asyncssh")
    async def test_connect_with_proxy_jump(self, mock_asyncssh):
        mock_jump_conn = AsyncMock()
        mock_target_conn = AsyncMock()
        mock_asyncssh.connect = AsyncMock(
            side_effect=[mock_jump_conn, mock_target_conn]
        )
        mock_target_conn.forward_local_port = AsyncMock(return_value=MagicMock())

        peer_configs = {
            "bastion": {
                "ssh_host": "bastion.example.com",
                "ssh_user": "admin",
                "ssh_port": 22,
            }
        }

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            proxy_jump="bastion",
            peer_configs=peer_configs,
            local_port=19100,
        )
        await t.connect()

        assert mock_asyncssh.connect.await_count == 2
        # First call: jump host
        jump_kwargs = mock_asyncssh.connect.call_args_list[0][1]
        assert jump_kwargs["host"] == "bastion.example.com"
        assert jump_kwargs["username"] == "admin"
        # Second call: target with tunnel
        target_kwargs = mock_asyncssh.connect.call_args_list[1][1]
        assert target_kwargs["host"] == "10.0.1.8"
        assert target_kwargs["tunnel"] is mock_jump_conn

    @patch("head.transport.ssh.asyncssh")
    async def test_connect_with_custom_ssh_port(self, mock_asyncssh):
        mock_conn = AsyncMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_conn.forward_local_port = AsyncMock(return_value=MagicMock())

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            ssh_port=2222,
            local_port=19100,
        )
        await t.connect()

        call_kwargs = mock_asyncssh.connect.call_args[1]
        assert call_kwargs["port"] == 2222

    @patch("head.transport.ssh.asyncssh")
    async def test_close(self, mock_asyncssh):
        mock_conn = AsyncMock()
        mock_conn.is_closed = MagicMock(return_value=False)
        mock_conn.close = MagicMock()
        mock_listener = MagicMock()
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_conn.forward_local_port = AsyncMock(return_value=mock_listener)

        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
            local_port=19100,
        )
        await t.connect()
        await t.close()

        mock_listener.close.assert_called_once()
        mock_conn.close.assert_called_once()
        mock_conn.wait_closed.assert_awaited_once()
        assert t._conn is None
        assert t._listener is None

    @patch("head.transport.ssh.asyncssh")
    async def test_close_before_connect_is_noop(self, mock_asyncssh):
        t = SSHTransport(
            peer_id="lab",
            ssh_host="10.0.1.8",
            ssh_user="youruser",
        )
        # Should not raise
        await t.close()
