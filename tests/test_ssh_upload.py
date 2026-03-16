"""
Tests for SSH Manager upload_files functionality.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from dataclasses import dataclass

from head.ssh_manager import SSHManager, SSHTunnel
from head.config import Config, MachineConfig, FilePoolConfig
from head.file_pool import FileEntry


# ─── Fixtures ───


@pytest.fixture
def mock_config():
    config = Config()
    config.machines = {
        "gpu-1": MachineConfig(id="gpu-1", host="10.0.0.1", user="user"),
    }
    config.file_pool = FilePoolConfig(
        remote_dir="/tmp/remote-code/files"
    )
    return config


@pytest.fixture
def ssh_manager(mock_config):
    return SSHManager(mock_config)


@pytest.fixture
def mock_tunnel():
    """Create a mock SSH tunnel with a mock connection."""
    conn = AsyncMock()
    conn.run = AsyncMock()
    conn.is_closed = MagicMock(return_value=False)
    listener = MagicMock()
    tunnel = SSHTunnel("gpu-1", 19100, conn, listener)
    return tunnel


@pytest.fixture
def mock_file_entry(tmp_path):
    """Create a mock FileEntry with a real file."""
    test_file = tmp_path / "report.pdf"
    test_file.write_bytes(b"PDF content here")
    return FileEntry(
        file_id="sess1234_abcd5678",
        original_name="report.pdf",
        local_path=test_file,
        size=len(b"PDF content here"),
        mime_type="application/pdf",
        created_at=1000.0,
    )


# ─── upload_files Tests ───


class TestUploadFiles:
    @pytest.mark.asyncio
    async def test_upload_single_file(self, ssh_manager, mock_tunnel, mock_file_entry):
        ssh_manager.tunnels["gpu-1"] = mock_tunnel

        with patch("head.ssh_manager.asyncssh") as mock_asyncssh:
            mock_asyncssh.scp = AsyncMock()
            result = await ssh_manager.upload_files("gpu-1", [mock_file_entry])

        assert "sess1234_abcd5678" in result
        assert result["sess1234_abcd5678"] == "/tmp/remote-code/files/sess1234_abcd5678_report.pdf"

        # Should have created the remote directory
        mock_tunnel.conn.run.assert_called_once_with("mkdir -p /tmp/remote-code/files")

    @pytest.mark.asyncio
    async def test_upload_multiple_files(self, ssh_manager, mock_tunnel, tmp_path):
        ssh_manager.tunnels["gpu-1"] = mock_tunnel

        entries = []
        for i, name in enumerate(["doc1.pdf", "img.png"]):
            f = tmp_path / name
            f.write_bytes(b"x" * 10)
            entries.append(FileEntry(
                file_id=f"id_{i}",
                original_name=name,
                local_path=f,
                size=10,
                mime_type="application/pdf",
                created_at=1000.0 + i,
            ))

        with patch("head.ssh_manager.asyncssh") as mock_asyncssh:
            mock_asyncssh.scp = AsyncMock()
            result = await ssh_manager.upload_files("gpu-1", entries)

        assert len(result) == 2
        assert result["id_0"] == "/tmp/remote-code/files/id_0_doc1.pdf"
        assert result["id_1"] == "/tmp/remote-code/files/id_1_img.png"

    @pytest.mark.asyncio
    async def test_upload_custom_remote_base(self, ssh_manager, mock_tunnel, mock_file_entry):
        ssh_manager.tunnels["gpu-1"] = mock_tunnel

        with patch("head.ssh_manager.asyncssh") as mock_asyncssh:
            mock_asyncssh.scp = AsyncMock()
            result = await ssh_manager.upload_files(
                "gpu-1", [mock_file_entry], remote_base="/custom/path"
            )

        assert result["sess1234_abcd5678"] == "/custom/path/sess1234_abcd5678_report.pdf"
        mock_tunnel.conn.run.assert_called_once_with("mkdir -p /custom/path")

    @pytest.mark.asyncio
    async def test_upload_no_tunnel_raises(self, ssh_manager, mock_file_entry):
        with pytest.raises(ValueError, match="No active tunnel"):
            await ssh_manager.upload_files("gpu-1", [mock_file_entry])

    @pytest.mark.asyncio
    async def test_upload_dead_tunnel_raises(self, ssh_manager, mock_file_entry):
        conn = AsyncMock()
        conn.is_closed = MagicMock(return_value=True)
        listener = MagicMock()
        dead_tunnel = SSHTunnel("gpu-1", 19100, conn, listener)
        ssh_manager.tunnels["gpu-1"] = dead_tunnel

        with pytest.raises(ValueError, match="No active tunnel"):
            await ssh_manager.upload_files("gpu-1", [mock_file_entry])

    @pytest.mark.asyncio
    async def test_upload_empty_list(self, ssh_manager, mock_tunnel):
        ssh_manager.tunnels["gpu-1"] = mock_tunnel

        with patch("head.ssh_manager.asyncssh") as mock_asyncssh:
            mock_asyncssh.scp = AsyncMock()
            result = await ssh_manager.upload_files("gpu-1", [])

        assert result == {}
        # mkdir should still be called
        mock_tunnel.conn.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_scp_called_with_correct_args(self, ssh_manager, mock_tunnel, mock_file_entry):
        ssh_manager.tunnels["gpu-1"] = mock_tunnel

        with patch("head.ssh_manager.asyncssh") as mock_asyncssh:
            mock_asyncssh.scp = AsyncMock()
            await ssh_manager.upload_files("gpu-1", [mock_file_entry])

            mock_asyncssh.scp.assert_called_once_with(
                str(mock_file_entry.local_path),
                (mock_tunnel.conn, "/tmp/remote-code/files/sess1234_abcd5678_report.pdf"),
            )
