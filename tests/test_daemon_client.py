"""
Tests for head/daemon_client.py
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import ClientSession

from head.daemon_client import DaemonClient, DaemonError, DaemonConnectionError


# ─── Helpers ───


class MockResponse:
    """Mock aiohttp response for regular JSON-RPC calls."""

    def __init__(self, data: dict, status: int = 200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockSSEResponse:
    """Mock aiohttp response for SSE streaming."""

    def __init__(self, lines: list[str], status: int = 200):
        self._lines = lines
        self.status = status
        self.content = self._make_content()

    def _make_content(self):
        async def aiter():
            for line in self._lines:
                yield (line + "\n").encode("utf-8")

        return aiter()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def client():
    return DaemonClient(timeout=10)


# ─── _rpc_call ───


class TestRpcCall:
    @pytest.mark.asyncio
    async def test_successful_response(self, client):
        mock_resp = MockResponse({"result": {"sessionId": "abc-123"}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            # Force create session
            client._session = aiohttp.ClientSession()
            try:
                result = await client._rpc_call(19100, "session.create", {"path": "/test"})
                assert result == {"sessionId": "abc-123"}
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_error_response(self, client):
        mock_resp = MockResponse({"error": {"message": "Session not found", "code": 404}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                with pytest.raises(DaemonError, match="Session not found"):
                    await client._rpc_call(19100, "session.destroy", {"sessionId": "bad"})
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_error_code_preserved(self, client):
        mock_resp = MockResponse({"error": {"message": "Not found", "code": 404}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                with pytest.raises(DaemonError) as exc_info:
                    await client._rpc_call(19100, "test.method")
                assert exc_info.value.code == 404
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_connection_error(self, client):
        # The code does `async with session.post(...) as resp:`.
        # aiohttp's session.post() returns an _RequestContextManager synchronously.
        # When the underlying request fails, the exception is raised inside __aenter__.
        # We mock _get_session to return a mock session whose post() returns
        # a context manager that raises on __aenter__.
        mock_session = MagicMock()
        cm = AsyncMock()
        cm.__aenter__.side_effect = aiohttp.ClientError("Connection refused")
        mock_session.post.return_value = cm

        with patch.object(client, "_get_session", return_value=mock_session):
            with pytest.raises(DaemonConnectionError, match="Failed to connect"):
                await client._rpc_call(19100, "health.check")

    @pytest.mark.asyncio
    async def test_empty_result(self, client):
        mock_resp = MockResponse({"result": {}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client._rpc_call(19100, "session.list")
                assert result == {}
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_no_result_key(self, client):
        mock_resp = MockResponse({})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client._rpc_call(19100, "session.list")
                assert result == {}
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_url_construction(self, client):
        assert client._url(19100) == "http://127.0.0.1:19100/rpc"
        assert client._url(9100) == "http://127.0.0.1:9100/rpc"


# ─── health_check ───


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        expected = {"ok": True, "sessions": 2, "uptime": 3600}
        mock_resp = MockResponse({"result": expected})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.health_check(19100)
                assert result == expected
            finally:
                await client._session.close()


# ─── monitor_sessions ───


class TestMonitorSessions:
    @pytest.mark.asyncio
    async def test_monitor_sessions(self, client):
        expected = {"sessions": [{"sessionId": "a", "status": "idle"}], "uptime": 100}
        mock_resp = MockResponse({"result": expected})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.monitor_sessions(19100)
                assert result == expected
            finally:
                await client._session.close()


# ─── create_session, destroy_session, list_sessions ───


class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_create_session(self, client):
        mock_resp = MockResponse({"result": {"sessionId": "new-sess-123"}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.create_session(19100, "/test/path", "auto")
                assert result == "new-sess-123"
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_destroy_session(self, client):
        mock_resp = MockResponse({"result": {"ok": True}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.destroy_session(19100, "sess-001")
                assert result is True
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_destroy_session_failed(self, client):
        mock_resp = MockResponse({"result": {"ok": False}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.destroy_session(19100, "sess-001")
                assert result is False
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        mock_resp = MockResponse(
            {
                "result": {
                    "sessions": [
                        {"sessionId": "s1", "status": "idle"},
                        {"sessionId": "s2", "status": "busy"},
                    ]
                }
            }
        )
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.list_sessions(19100)
                assert len(result) == 2
                assert result[0]["sessionId"] == "s1"
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client):
        mock_resp = MockResponse({"result": {}})
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                result = await client.list_sessions(19100)
                assert result == []
            finally:
                await client._session.close()


# ─── send_message (SSE) ───


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_normal_events(self, client):
        sse_lines = [
            'data: {"type": "partial", "content": "Hello"}',
            'data: {"type": "text", "content": "Hello world"}',
            'data: {"type": "result", "session_id": "sdk-123"}',
            "data: [DONE]",
        ]
        mock_resp = MockSSEResponse(sse_lines)
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                events = []
                async for event in client.send_message(19100, "sess-001", "Hi"):
                    events.append(event)

                assert len(events) == 3
                assert events[0] == {"type": "partial", "content": "Hello"}
                assert events[1] == {"type": "text", "content": "Hello world"}
                assert events[2] == {"type": "result", "session_id": "sdk-123"}
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_done_terminates_stream(self, client):
        sse_lines = [
            'data: {"type": "text", "content": "Hello"}',
            "data: [DONE]",
            'data: {"type": "text", "content": "Should not appear"}',
        ]
        mock_resp = MockSSEResponse(sse_lines)
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                events = []
                async for event in client.send_message(19100, "sess-001", "Hi"):
                    events.append(event)

                assert len(events) == 1
                assert events[0]["content"] == "Hello"
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_empty_lines_skipped(self, client):
        sse_lines = [
            "",
            'data: {"type": "text", "content": "Hello"}',
            "",
            "data: [DONE]",
        ]
        mock_resp = MockSSEResponse(sse_lines)
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                events = []
                async for event in client.send_message(19100, "sess-001", "Hi"):
                    events.append(event)

                assert len(events) == 1
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_json_parse_errors_skipped(self, client):
        sse_lines = [
            'data: {"type": "text", "content": "Hello"}',
            "data: {invalid json}",
            'data: {"type": "text", "content": "World"}',
            "data: [DONE]",
        ]
        mock_resp = MockSSEResponse(sse_lines)
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                events = []
                async for event in client.send_message(19100, "sess-001", "Hi"):
                    events.append(event)

                assert len(events) == 2
                assert events[0]["content"] == "Hello"
                assert events[1]["content"] == "World"
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_non_data_lines_skipped(self, client):
        sse_lines = [
            ": this is a comment",
            "event: ping",
            'data: {"type": "text", "content": "Hello"}',
            "data: [DONE]",
        ]
        mock_resp = MockSSEResponse(sse_lines)
        with patch.object(aiohttp.ClientSession, "post", return_value=mock_resp):
            client._session = aiohttp.ClientSession()
            try:
                events = []
                async for event in client.send_message(19100, "sess-001", "Hi"):
                    events.append(event)

                assert len(events) == 1
            finally:
                await client._session.close()

    @pytest.mark.asyncio
    async def test_timeout_yields_error_event(self, client):
        mock_session = MagicMock()
        cm = AsyncMock()
        cm.__aenter__.side_effect = asyncio.TimeoutError()
        mock_session.post.return_value = cm

        with patch.object(client, "_get_session", return_value=mock_session):
            events = []
            async for event in client.send_message(19100, "sess-001", "Hi", idle_timeout=1):
                events.append(event)

            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert "timeout" in events[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_connection_error_yields_error_event(self, client):
        mock_session = MagicMock()
        cm = AsyncMock()
        cm.__aenter__.side_effect = aiohttp.ClientError("Connection lost")
        mock_session.post.return_value = cm

        with patch.object(client, "_get_session", return_value=mock_session):
            events = []
            async for event in client.send_message(19100, "sess-001", "Hi"):
                events.append(event)

            assert len(events) == 1
            assert events[0]["type"] == "error"
            assert "Connection" in events[0]["message"]


# ─── close ───


class TestClose:
    @pytest.mark.asyncio
    async def test_close_session(self, client):
        client._session = aiohttp.ClientSession()
        assert not client._session.closed
        await client.close()
        assert client._session.closed

    @pytest.mark.asyncio
    async def test_close_no_session(self, client):
        # Should not raise
        await client.close()

    @pytest.mark.asyncio
    async def test_close_already_closed(self, client):
        client._session = aiohttp.ClientSession()
        await client._session.close()
        # Should not raise
        await client.close()


# ─── _get_session ───


class TestGetSession:
    @pytest.mark.asyncio
    async def test_creates_session(self, client):
        session = await client._get_session()
        assert session is not None
        assert not session.closed
        await session.close()

    @pytest.mark.asyncio
    async def test_reuses_session(self, client):
        s1 = await client._get_session()
        s2 = await client._get_session()
        assert s1 is s2
        await s1.close()

    @pytest.mark.asyncio
    async def test_recreates_closed_session(self, client):
        s1 = await client._get_session()
        await s1.close()
        s2 = await client._get_session()
        assert s2 is not s1
        assert not s2.closed
        await s2.close()


# ─── V2: extra_headers and base_url ───


class TestDaemonClientV2:
    def test_extra_headers_stored(self):
        client = DaemonClient(extra_headers={"Authorization": "Bearer ccast_test"})
        assert client._extra_headers["Authorization"] == "Bearer ccast_test"

    def test_extra_headers_default_empty(self):
        client = DaemonClient()
        assert client._extra_headers == {}

    def test_base_url_override(self):
        client = DaemonClient(base_url="https://10.0.1.5:9100")
        assert client._url() == "https://10.0.1.5:9100/rpc"

    def test_base_url_trailing_slash_stripped(self):
        client = DaemonClient(base_url="https://10.0.1.5:9100/")
        assert client._url() == "https://10.0.1.5:9100/rpc"

    def test_backwards_compat_local_port(self):
        client = DaemonClient()
        assert client._url(19100) == "http://127.0.0.1:19100/rpc"

    def test_base_url_ignores_local_port(self):
        client = DaemonClient(base_url="https://remote:9100")
        assert client._url(19100) == "https://remote:9100/rpc"

    @pytest.mark.asyncio
    async def test_extra_headers_sent_in_rpc_call(self):
        client = DaemonClient(extra_headers={"Authorization": "Bearer tok123"})
        mock_session = MagicMock()
        mock_resp = MockResponse({"result": {"ok": True}})
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", return_value=mock_session):
            await client._rpc_call(19100, "health.check")

        # Verify headers were passed
        call_kwargs = mock_session.post.call_args
        assert "headers" in call_kwargs.kwargs or (len(call_kwargs.args) > 1)
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer tok123"
        assert headers.get("Content-Type") == "application/json"
