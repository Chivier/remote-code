"""
Tests for head/token_manager.py
"""

import os
import stat
import pytest
from head.token_manager import generate_token, TokenManager


class TestGenerateToken:
    def test_format(self):
        """Token starts with 'ccast_' and has total length 70 (6 prefix + 64 hex)."""
        token = generate_token()
        assert token.startswith("ccast_")
        assert len(token) == 70
        # The hex portion should be valid hex
        hex_part = token[6:]
        int(hex_part, 16)  # raises ValueError if not hex

    def test_tokens_are_unique(self):
        """Multiple calls produce distinct tokens."""
        tokens = {generate_token() for _ in range(50)}
        assert len(tokens) == 50


class TestTokenManager:
    @pytest.fixture
    def manager(self, tmp_path):
        tokens_file = tmp_path / "tokens.yaml"
        return TokenManager(str(tokens_file))

    def test_add_and_list(self, manager):
        token = manager.add("machine-a")
        assert token.startswith("ccast_")
        items = manager.list()
        assert len(items) == 1
        assert items[0]["label"] == "machine-a"
        assert items[0]["token"] == token
        assert "created" in items[0]

    def test_validate(self, manager):
        token = manager.add("test-label")
        assert manager.validate(token) is True
        assert manager.validate("ccast_bogus") is False

    def test_revoke_token(self, manager):
        token = manager.add("to-revoke")
        assert manager.revoke(token) is True
        assert manager.validate(token) is False
        assert manager.list() == []

    def test_revoke_nonexistent(self, manager):
        assert manager.revoke("ccast_doesnotexist") is False

    def test_file_permissions(self, tmp_path):
        tokens_file = tmp_path / "subdir" / "tokens.yaml"
        mgr = TokenManager(str(tokens_file))
        mgr.add("perm-test")
        # File should be 0600
        file_mode = stat.S_IMODE(os.stat(str(tokens_file)).st_mode)
        assert file_mode == 0o600
        # Parent dir should be 0700
        dir_mode = stat.S_IMODE(os.stat(str(tokens_file.parent)).st_mode)
        assert dir_mode == 0o700

    def test_persistence(self, tmp_path):
        tokens_file = str(tmp_path / "tokens.yaml")
        mgr1 = TokenManager(tokens_file)
        token = mgr1.add("persistent")
        # Load a fresh instance from the same file
        mgr2 = TokenManager(tokens_file)
        assert mgr2.validate(token) is True
        items = mgr2.list()
        assert len(items) == 1
        assert items[0]["label"] == "persistent"
