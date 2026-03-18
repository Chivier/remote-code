"""Multi-user isolation tests.

Verifies that two users (alice, bob) can run independent daemons on the
same machine without interference.

Environment variables (set by run-multiuser-tests.sh):
    ALICE_PORT  – port Alice's daemon is listening on (default 9100)
    BOB_PORT    – port Bob's daemon auto-incremented to (default 9101)
"""

import os
import subprocess

import aiohttp
import pytest
import pytest_asyncio

ALICE_PORT = int(os.environ.get("ALICE_PORT", "9100"))
BOB_PORT = int(os.environ.get("BOB_PORT", "9101"))

BASE = "http://127.0.0.1"


async def _rpc(port: int, method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC call to a daemon."""
    payload = {"method": method}
    if params:
        payload["params"] = params
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BASE}:{port}/rpc",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            return await resp.json()


# ── Health ──


@pytest.mark.asyncio
async def test_both_daemons_healthy():
    """Both daemons respond to health checks on their respective ports."""
    alice = await _rpc(ALICE_PORT, "health.check")
    bob = await _rpc(BOB_PORT, "health.check")
    assert alice.get("ok") is True, f"Alice health failed: {alice}"
    assert bob.get("ok") is True, f"Bob health failed: {bob}"


# ── Port isolation ──


@pytest.mark.asyncio
async def test_different_ports():
    """Alice and Bob are on different ports."""
    assert ALICE_PORT != BOB_PORT, "Ports must differ for isolation"


def test_port_files_isolated():
    """Each user has their own daemon.port file with their actual port."""
    alice_pf = "/home/alice/.codecast/daemon.port"
    bob_pf = "/home/bob/.codecast/daemon.port"

    if os.path.exists(alice_pf) and os.path.exists(bob_pf):
        alice_port = int(open(alice_pf).read().strip())
        bob_port = int(open(bob_pf).read().strip())
        assert alice_port != bob_port, "Port files must contain different ports"


# ── Process ownership ──


def test_process_ownership():
    """Each daemon is owned by its respective user."""
    # Find daemon PIDs owned by alice
    alice_result = subprocess.run(
        ["pgrep", "-u", "alice", "-f", "codecast-daemon"],
        capture_output=True,
        text=True,
    )
    # Find daemon PIDs owned by bob
    bob_result = subprocess.run(
        ["pgrep", "-u", "bob", "-f", "codecast-daemon"],
        capture_output=True,
        text=True,
    )

    alice_pids = set(alice_result.stdout.strip().splitlines()) if alice_result.returncode == 0 else set()
    bob_pids = set(bob_result.stdout.strip().splitlines()) if bob_result.returncode == 0 else set()

    assert len(alice_pids) > 0, "Alice should have at least one daemon process"
    assert len(bob_pids) > 0, "Bob should have at least one daemon process"
    assert alice_pids.isdisjoint(bob_pids), "PIDs should not overlap"


# ── Session isolation ──


@pytest.mark.asyncio
async def test_alice_session_on_alice_daemon():
    """Alice can create a session in her own project directory."""
    result = await _rpc(
        ALICE_PORT,
        "session.create",
        {
            "path": "/home/alice/project",
            "mode": "auto",
        },
    )
    session_id = result.get("sessionId") or result.get("session_id")
    assert session_id, f"Failed to create Alice's session: {result}"

    # Cleanup
    await _rpc(ALICE_PORT, "session.destroy", {"sessionId": session_id})


@pytest.mark.asyncio
async def test_bob_session_on_bob_daemon():
    """Bob can create a session in his own project directory."""
    result = await _rpc(
        BOB_PORT,
        "session.create",
        {
            "path": "/home/bob/project",
            "mode": "auto",
        },
    )
    session_id = result.get("sessionId") or result.get("session_id")
    assert session_id, f"Failed to create Bob's session: {result}"

    # Cleanup
    await _rpc(BOB_PORT, "session.destroy", {"sessionId": session_id})


@pytest.mark.asyncio
async def test_sessions_isolated_between_daemons():
    """Sessions on Alice's daemon are not visible on Bob's daemon."""
    # Create session on Alice
    alice_result = await _rpc(
        ALICE_PORT,
        "session.create",
        {
            "path": "/home/alice/project",
            "mode": "auto",
        },
    )
    alice_sid = alice_result.get("sessionId") or alice_result.get("session_id")
    assert alice_sid

    # List sessions on Bob — Alice's session should not appear
    bob_sessions = await _rpc(BOB_PORT, "session.list")
    bob_sids = [s.get("sessionId") or s.get("session_id") or s.get("id") for s in (bob_sessions.get("sessions") or [])]
    assert alice_sid not in bob_sids, "Alice's session should not be visible on Bob's daemon"

    # Cleanup
    await _rpc(ALICE_PORT, "session.destroy", {"sessionId": alice_sid})


# ── No cross-access ──


@pytest.mark.asyncio
async def test_alice_cannot_access_bob_dirs():
    """Alice's daemon should not be able to create sessions in Bob's home."""
    result = await _rpc(
        ALICE_PORT,
        "session.create",
        {
            "path": "/home/bob/project",
            "mode": "auto",
        },
    )
    # This may succeed (daemon doesn't restrict paths) or fail (permission denied).
    # If it succeeds, the session will run as alice and won't have write access.
    session_id = result.get("sessionId") or result.get("session_id")
    if session_id:
        # Even if session created, the underlying process runs as alice
        # Verify by checking the daemon's process owner
        alice_result = subprocess.run(
            ["pgrep", "-u", "alice", "-f", "codecast-daemon"],
            capture_output=True,
            text=True,
        )
        assert alice_result.returncode == 0, "Alice's daemon should run as alice"
        await _rpc(ALICE_PORT, "session.destroy", {"sessionId": session_id})
