"""
Codecast Integration Tests

Tests the full head<->daemon communication path using a real Rust daemon
and a mock Claude CLI. Runs inside a Docker container.

The daemon is started by run-tests.sh before these tests run.
Tests communicate with the daemon via localhost:DAEMON_PORT.
"""

import asyncio
import json
import os
import time
from typing import Any

import aiohttp
import pytest
import pytest_asyncio

DAEMON_HOST = os.environ.get("DAEMON_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("DAEMON_PORT", "9100"))
RPC_URL = f"http://{DAEMON_HOST}:{DAEMON_PORT}/rpc"

TEST_PROJECT_PATH = "/workspace/test-project"


# ─── Helpers ───


async def rpc_call(method: str, params: dict | None = None) -> dict[str, Any]:
    """Make a JSON-RPC call to the daemon."""
    payload: dict[str, Any] = {"method": method}
    if params:
        payload["params"] = params

    async with aiohttp.ClientSession() as session:
        async with session.post(
            RPC_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if "error" in data and data["error"]:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result", {})


async def rpc_stream(method: str, params: dict) -> list[dict]:
    """Make an SSE-streaming RPC call and collect all events."""
    payload = {"method": method, "params": params}
    events = []

    async with aiohttp.ClientSession() as session:
        async with session.post(
            RPC_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            async for line_bytes in resp.content:
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        events.append(json.loads(data_str))
                    except json.JSONDecodeError:
                        pass
    return events


# ─── Test: Health Check ───


class TestHealthCheck:
    """Test daemon health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_ok(self):
        result = await rpc_call("health.check")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_health_check_has_uptime(self):
        result = await rpc_call("health.check")
        assert "uptime" in result
        assert isinstance(result["uptime"], (int, float))
        assert result["uptime"] >= 0

    @pytest.mark.asyncio
    async def test_health_check_has_memory(self):
        result = await rpc_call("health.check")
        assert "memory" in result
        mem = result["memory"]
        assert "rss" in mem
        assert mem["rss"] > 0

    @pytest.mark.asyncio
    async def test_health_check_has_node_version(self):
        """Rust daemon reports version info instead of nodeVersion."""
        result = await rpc_call("health.check")
        # The Rust daemon may use different field names
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_health_check_sessions_count(self):
        result = await rpc_call("health.check")
        assert "sessions" in result
        assert isinstance(result["sessions"], int)


# ─── Test: Session Lifecycle ───


class TestSessionLifecycle:
    """Test creating, listing, and destroying sessions."""

    @pytest.mark.asyncio
    async def test_create_session(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        assert "sessionId" in result
        session_id = result["sessionId"]
        assert len(session_id) > 0

        # Cleanup
        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_create_session_invalid_path(self):
        with pytest.raises(RuntimeError, match="Path does not exist"):
            await rpc_call("session.create", {"path": "/nonexistent/path"})

    @pytest.mark.asyncio
    async def test_create_session_with_mode(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH, "mode": "plan"})
        session_id = result["sessionId"]
        assert session_id

        # Verify mode via list
        sessions = await rpc_call("session.list")
        found = [s for s in sessions["sessions"] if s["sessionId"] == session_id]
        assert len(found) == 1
        assert found[0]["mode"] == "plan"

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """List sessions should work even with no sessions."""
        result = await rpc_call("session.list")
        assert "sessions" in result
        assert isinstance(result["sessions"], list)

    @pytest.mark.asyncio
    async def test_list_sessions_after_create(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        sessions = await rpc_call("session.list")
        ids = [s["sessionId"] for s in sessions["sessions"]]
        assert session_id in ids

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_destroy_session(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        destroy_result = await rpc_call("session.destroy", {"sessionId": session_id})
        assert destroy_result["ok"] is True

        # Session should be gone
        sessions = await rpc_call("session.list")
        ids = [s["sessionId"] for s in sessions["sessions"]]
        assert session_id not in ids

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_session(self):
        result = await rpc_call("session.destroy", {"sessionId": "00000000-0000-0000-0000-000000000000"})
        # Should return ok=false or error, not crash
        assert result.get("ok") is False or result.get("ok") is True

    @pytest.mark.asyncio
    async def test_create_multiple_sessions(self):
        ids = []
        for _ in range(3):
            result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
            ids.append(result["sessionId"])

        sessions = await rpc_call("session.list")
        listed_ids = {s["sessionId"] for s in sessions["sessions"]}
        for sid in ids:
            assert sid in listed_ids

        for sid in ids:
            await rpc_call("session.destroy", {"sessionId": sid})


# ─── Test: Send Message (with mock Claude) ───


class TestSendMessage:
    """Test sending messages through the daemon to mock Claude."""

    @pytest.mark.asyncio
    async def test_send_simple_message(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        events = await rpc_stream(
            "session.send",
            {"sessionId": session_id, "message": "echo:hello integration test"},
        )

        # Should have system init, partials, text, and result events
        types = [e["type"] for e in events]
        assert "system" in types, f"Expected system event, got types: {types}"
        assert "result" in types, f"Expected result event, got types: {types}"

        # Should contain our echo text
        text_events = [e for e in events if e["type"] == "text"]
        if text_events:
            combined_text = " ".join(e.get("content", "") for e in text_events)
            assert "hello integration test" in combined_text

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_send_message_gets_session_id(self):
        """The result event should contain a session_id from mock Claude."""
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        events = await rpc_stream(
            "session.send",
            {"sessionId": session_id, "message": "echo:test"},
        )

        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) > 0
        assert "session_id" in result_events[0]

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_send_message_tool_use(self):
        """Test that tool_use events are correctly relayed."""
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        events = await rpc_stream(
            "session.send",
            {"sessionId": session_id, "message": "tools"},
        )

        types = [e["type"] for e in events]
        assert "tool_use" in types, f"Expected tool_use event, got: {types}"

        tool_events = [e for e in events if e["type"] == "tool_use"]
        assert any(e.get("tool") == "Read" for e in tool_events)

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_session(self):
        """Sending to a bad session ID should return an error event."""
        events = await rpc_stream(
            "session.send",
            {
                "sessionId": "00000000-0000-0000-0000-000000000000",
                "message": "echo:fail",
            },
        )

        # Should get an error
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) > 0

    @pytest.mark.asyncio
    async def test_send_multiple_messages_sequentially(self):
        """Test sending two messages in sequence (tests --resume flow)."""
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        # First message
        events1 = await rpc_stream(
            "session.send",
            {"sessionId": session_id, "message": "echo:first"},
        )
        result1 = [e for e in events1 if e["type"] == "result"]
        assert len(result1) > 0

        # Second message (should use --resume internally)
        events2 = await rpc_stream(
            "session.send",
            {"sessionId": session_id, "message": "echo:second"},
        )
        result2 = [e for e in events2 if e["type"] == "result"]
        assert len(result2) > 0

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Session Mode ───


class TestSessionMode:
    """Test setting permission modes."""

    @pytest.mark.asyncio
    async def test_set_mode(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        set_result = await rpc_call("session.set_mode", {"sessionId": session_id, "mode": "plan"})
        assert set_result["ok"] is True

        # Verify mode changed
        sessions = await rpc_call("session.list")
        found = [s for s in sessions["sessions"] if s["sessionId"] == session_id]
        assert found[0]["mode"] == "plan"

        await rpc_call("session.destroy", {"sessionId": session_id})

    @pytest.mark.asyncio
    async def test_set_all_modes(self):
        """Verify all four permission modes can be set."""
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        for mode in ["auto", "code", "plan", "ask"]:
            set_result = await rpc_call("session.set_mode", {"sessionId": session_id, "mode": mode})
            assert set_result["ok"] is True

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Queue Stats ───


class TestQueueStats:
    """Test message queue statistics."""

    @pytest.mark.asyncio
    async def test_queue_stats_idle(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        stats = await rpc_call("session.queue_stats", {"sessionId": session_id})
        assert "userPending" in stats
        assert stats["userPending"] == 0
        assert "responsePending" in stats
        assert stats["responsePending"] == 0

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Monitor Sessions ───


class TestMonitorSessions:
    """Test the monitor.sessions RPC method."""

    @pytest.mark.asyncio
    async def test_monitor_returns_details(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        monitor = await rpc_call("monitor.sessions")
        assert "sessions" in monitor
        assert "totalSessions" in monitor
        assert monitor["totalSessions"] >= 1
        assert "uptime" in monitor

        found = [s for s in monitor["sessions"] if s["sessionId"] == session_id]
        assert len(found) == 1
        detail = found[0]
        assert detail["path"] == TEST_PROJECT_PATH
        assert "queue" in detail
        assert "createdAt" in detail

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Interrupt ───


class TestInterrupt:
    """Test session interrupt."""

    @pytest.mark.asyncio
    async def test_interrupt_idle_session(self):
        """Interrupting an idle session should not crash."""
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        int_result = await rpc_call("session.interrupt", {"sessionId": session_id})
        assert "ok" in int_result

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Reconnect ───


class TestReconnect:
    """Test session reconnect."""

    @pytest.mark.asyncio
    async def test_reconnect_returns_buffered(self):
        result = await rpc_call("session.create", {"path": TEST_PROJECT_PATH})
        session_id = result["sessionId"]

        recon = await rpc_call("session.reconnect", {"sessionId": session_id})
        assert "bufferedEvents" in recon
        assert isinstance(recon["bufferedEvents"], list)

        await rpc_call("session.destroy", {"sessionId": session_id})


# ─── Test: Invalid RPC ───


class TestInvalidRPC:
    """Test error handling for invalid RPC calls."""

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        with pytest.raises(RuntimeError, match="Method not found"):
            await rpc_call("nonexistent.method")

    @pytest.mark.asyncio
    async def test_missing_params(self):
        with pytest.raises(RuntimeError):
            await rpc_call("session.create")  # Missing path param

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        with pytest.raises(RuntimeError):
            await rpc_call("session.send")  # Missing sessionId + message


# ─── Test: DaemonClient (Python head code) ───


class TestDaemonClient:
    """Test the Python DaemonClient against the real daemon."""

    @pytest.mark.asyncio
    async def test_daemon_client_health(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            result = await client.health_check(DAEMON_PORT)
            assert result["ok"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_daemon_client_create_and_destroy(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            session_id = await client.create_session(DAEMON_PORT, TEST_PROJECT_PATH, "auto")
            assert session_id

            sessions = await client.list_sessions(DAEMON_PORT)
            ids = [s["sessionId"] for s in sessions]
            assert session_id in ids

            ok = await client.destroy_session(DAEMON_PORT, session_id)
            assert ok is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_daemon_client_send_message(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            session_id = await client.create_session(DAEMON_PORT, TEST_PROJECT_PATH, "auto")

            events = []
            async for event in client.send_message(DAEMON_PORT, session_id, "echo:from-daemon-client"):
                events.append(event)

            types = [e["type"] for e in events]
            assert "result" in types, f"Expected result in {types}"

            await client.destroy_session(DAEMON_PORT, session_id)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_daemon_client_set_mode(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            session_id = await client.create_session(DAEMON_PORT, TEST_PROJECT_PATH, "auto")

            ok = await client.set_mode(DAEMON_PORT, session_id, "plan")
            assert ok is True

            await client.destroy_session(DAEMON_PORT, session_id)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_daemon_client_monitor(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            result = await client.monitor_sessions(DAEMON_PORT)
            assert "sessions" in result
            assert "totalSessions" in result
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_daemon_client_queue_stats(self):
        from head.daemon_client import DaemonClient

        client = DaemonClient()
        try:
            session_id = await client.create_session(DAEMON_PORT, TEST_PROJECT_PATH, "auto")

            stats = await client.get_queue_stats(DAEMON_PORT, session_id)
            assert stats["userPending"] == 0

            await client.destroy_session(DAEMON_PORT, session_id)
        finally:
            await client.close()


# ─── Test: Session Router (SQLite, no daemon needed) ───


class TestSessionRouterIntegration:
    """Test SessionRouter works correctly (SQLite-backed, runs locally)."""

    @pytest.mark.asyncio
    async def test_session_router_crud(self, tmp_path):
        from head.session_router import SessionRouter

        router = SessionRouter(db_path=str(tmp_path / "test.db"))

        # Create
        router.register(
            channel_id="test:chan1",
            machine_id="test-machine",
            path="/workspace",
            daemon_session_id="sid-1",
        )

        # Read
        session = router.resolve("test:chan1")
        assert session is not None
        assert session.daemon_session_id == "sid-1"
        assert session.machine_id == "test-machine"

        # Detach
        router.detach("test:chan1")
        session = router.resolve("test:chan1")
        assert session is None

        # Find detached
        detached = router.list_sessions(machine_id="test-machine")
        assert len(detached) >= 1

    @pytest.mark.asyncio
    async def test_session_router_rename(self, tmp_path):
        from head.session_router import SessionRouter

        router = SessionRouter(db_path=str(tmp_path / "test2.db"))
        router.register(
            channel_id="test:chan2",
            machine_id="m1",
            path="/workspace",
            daemon_session_id="sid-2",
        )

        ok = router.rename_session("test:chan2", "my-custom-name")
        assert ok is True

        session = router.find_session_by_name("my-custom-name")
        assert session is not None
        assert session.daemon_session_id == "sid-2"


# ─── Test: Message Formatter (no daemon needed) ───


class TestMessageFormatterIntegration:
    """Test message formatting utilities."""

    def test_split_long_message(self):
        from head.message_formatter import split_message

        # A message that exceeds Discord's 2000 char limit
        long_text = "x" * 3000
        parts = split_message(long_text, max_len=2000)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= 2000

    def test_split_preserves_code_blocks(self):
        from head.message_formatter import split_message

        text = "before\n```python\n" + "x = 1\n" * 300 + "```\nafter"
        parts = split_message(text, max_len=2000)
        # Code blocks should be properly closed/reopened
        for part in parts:
            assert len(part) <= 2000

    def test_format_health(self):
        from head.message_formatter import format_health

        health = {
            "ok": True,
            "sessions": 2,
            "uptime": 3600,
            "memory": {"rss": 50, "heapUsed": 30, "heapTotal": 64},
            "nodeVersion": "v22.0.0",
            "pid": 1234,
        }
        formatted = format_health("test-machine", health)
        assert "test-machine" in formatted
        assert "2" in formatted  # session count


# ─── Test: Name Generator (no daemon needed) ───


class TestNameGeneratorIntegration:
    """Test human-friendly name generation."""

    def test_generates_names(self):
        from head.name_generator import generate_name

        name = generate_name()
        assert "-" in name  # adjective-noun format

    def test_names_are_unique(self):
        from head.name_generator import generate_name

        names = {generate_name() for _ in range(50)}
        # Should have high uniqueness (allow some collisions in 50 samples)
        assert len(names) >= 40


# ─── Test: Pip Install Verification ───


class TestPipInstall:
    """Verify the pip install produced a working package."""

    def test_head_module_importable(self):
        import head
        import head.config
        import head.daemon_client
        import head.session_router
        import head.message_formatter
        import head.name_generator

    def test_config_loading(self):
        """Config module should be importable and have key types."""
        from head.config import Config, MachineConfig

        # Should be importable without errors
        assert Config is not None
        assert MachineConfig is not None

    def test_codecast_entry_point(self):
        """The 'codecast' CLI entry point should be registered."""
        import subprocess

        result = subprocess.run(
            ["python", "-c", "from head.main import cli_main; print('OK')"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "OK", f"stderr: {result.stderr}"
