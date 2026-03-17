"""
Token manager for Codecast auth token CRUD.
Generates, stores, validates, and revokes bearer tokens used for
daemon authentication.
"""

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Generate a new token: 'ccast_' prefix + 64 hex characters (total 70 chars)."""
    return "ccast_" + secrets.token_hex(32)


class TokenManager:
    """Manage auth tokens persisted in a YAML file.

    Token file format::

        tokens:
          - token: ccast_...
            label: "machine-a"
            created: 2026-03-17T12:00:00+00:00
    """

    def __init__(self, tokens_file: str) -> None:
        self._path = Path(tokens_file)
        self._tokens: list[dict] = []
        self._load()

    # ── public API ───────────────────────────────────────────────

    def add(self, label: str) -> str:
        """Create a new token with the given label. Returns the token string."""
        token = generate_token()
        entry = {
            "token": token,
            "label": label,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self._tokens.append(entry)
        self._save()
        logger.info("Added token label=%s", label)
        return token

    def revoke(self, token: str) -> bool:
        """Revoke (delete) a token. Returns True if found and removed."""
        for i, entry in enumerate(self._tokens):
            if entry["token"] == token:
                self._tokens.pop(i)
                self._save()
                logger.info("Revoked token label=%s", entry["label"])
                return True
        return False

    def list(self) -> list[dict]:
        """Return a list of token entries (each with token, label, created)."""
        return list(self._tokens)

    def validate(self, token: str) -> bool:
        """Check whether a token is currently valid."""
        return any(entry["token"] == token for entry in self._tokens)

    # ── internal ─────────────────────────────────────────────────

    def _load(self) -> None:
        """Load tokens from the YAML file if it exists."""
        if self._path.exists():
            with open(self._path, "r") as f:
                data = yaml.safe_load(f) or {}
            self._tokens = data.get("tokens", [])
        else:
            self._tokens = []

    def _save(self) -> None:
        """Persist tokens to the YAML file with 0600 permissions. Parent dir gets 0700."""
        # Ensure parent directory exists with restricted permissions
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self._path.parent), 0o700)

        with open(self._path, "w") as f:
            yaml.safe_dump({"tokens": self._tokens}, f, default_flow_style=False)

        os.chmod(str(self._path), 0o600)
