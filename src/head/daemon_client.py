"""
Daemon RPC Client - communicates with Remote Agent Daemon over SSH tunnel.

Sends JSON-RPC requests to the daemon's HTTP server and handles
both regular JSON responses and SSE streaming responses.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import aiohttp

logger = logging.getLogger(__name__)


class DaemonClient:
    """JSON-RPC client for communicating with Remote Agent Daemon."""

    def __init__(
        self,
        timeout: int = 300,
        extra_headers: Optional[dict[str, str]] = None,
        base_url: Optional[str] = None,
    ):
        """
        Args:
            timeout: Default timeout in seconds for RPC calls.
                     Longer timeout for send_message (Claude can think for a while).
            extra_headers: Additional headers merged into every HTTP request
                           (e.g. Authorization for token auth).
            base_url: If set, _url() uses this instead of localhost:local_port.
                      Useful for direct remote access without SSH tunnels.
        """
        self.timeout = timeout
        self._extra_headers: dict[str, str] = extra_headers or {}
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    def _url(self, local_port: int = 0) -> str:
        """Build URL for RPC endpoint via SSH tunnel or base_url override."""
        if self._base_url:
            return f"{self._base_url.rstrip('/')}/rpc"
        return f"http://127.0.0.1:{local_port}/rpc"

    async def _rpc_call(self, local_port: int, method: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a JSON-RPC call and return the result."""
        session = await self._get_session()
        payload: dict[str, Any] = {"method": method}
        if params:
            payload["params"] = params

        headers = {"Content-Type": "application/json"}
        headers.update(self._extra_headers)

        try:
            async with session.post(
                self._url(local_port),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data: dict[str, Any] = await resp.json()

                if "error" in data and data["error"]:
                    error = data["error"]
                    raise DaemonError(
                        error.get("message", "Unknown error"),
                        error.get("code", -1),
                    )

                return data.get("result", {})

        except aiohttp.ClientError as e:
            raise DaemonConnectionError(f"Failed to connect to daemon on port {local_port}: {e}") from e

    # ─── Session Management ───

    async def create_session(self, local_port: int, path: str, mode: str = "auto", model: str | None = None) -> str:
        """
        Create a new Claude session on the remote machine.

        Returns:
            sessionId: The daemon's session ID.
        """
        params = {"path": path, "mode": mode}
        if model:
            params["model"] = model
        result = await self._rpc_call(
            local_port,
            "session.create",
            params,
        )
        session_id: str = result["sessionId"]
        logger.info(f"Created session {session_id} at {path}")
        return session_id

    async def send_message(
        self,
        local_port: int,
        session_id: str,
        message: str,
        idle_timeout: int = 300,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Send a message to a Claude session and stream back events.

        This uses SSE (Server-Sent Events) for streaming.
        Yields parsed event dicts.

        Args:
            idle_timeout: Max seconds to wait with no events before giving up.
                          Resets on every received event. Default 5 minutes.
        """
        session = await self._get_session()
        payload = {
            "method": "session.send",
            "params": {"sessionId": session_id, "message": message},
        }

        headers = {"Content-Type": "application/json"}
        headers.update(self._extra_headers)

        try:
            async with session.post(
                self._url(local_port),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=900,  # 15 minute total timeout
                    sock_read=idle_timeout,  # per-read timeout (idle detection)
                ),
            ) as resp:
                # Read SSE stream
                async for line_bytes in resp.content:
                    line = line_bytes.decode("utf-8").strip()

                    if not line:
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]  # Remove "data: " prefix

                        if data_str == "[DONE]":
                            return

                        try:
                            event = json.loads(data_str)
                            yield event
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse SSE data: {data_str}")
                            continue

        except asyncio.TimeoutError:
            yield {
                "type": "error",
                "message": f"Stream idle timeout ({idle_timeout}s with no events). Session may be stuck.",
            }
        except aiohttp.ClientError as e:
            yield {"type": "error", "message": f"Connection error: {e}"}

    async def resume_session(
        self, local_port: int, session_id: str, sdk_session_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Resume a session, with CodePilot-style fallback."""
        params: dict[str, Any] = {"sessionId": session_id}
        if sdk_session_id:
            params["sdkSessionId"] = sdk_session_id

        result = await self._rpc_call(local_port, "session.resume", params)
        return result

    async def destroy_session(self, local_port: int, session_id: str) -> bool:
        """Destroy a session and kill the Claude process."""
        result = await self._rpc_call(
            local_port,
            "session.destroy",
            {
                "sessionId": session_id,
            },
        )
        return bool(result.get("ok", False))

    async def list_sessions(self, local_port: int) -> list[dict[str, Any]]:
        """List all sessions on a remote daemon."""
        result = await self._rpc_call(local_port, "session.list")
        return result.get("sessions", [])

    async def set_mode(self, local_port: int, session_id: str, mode: str) -> bool:
        """Set the permission mode for a session."""
        result = await self._rpc_call(
            local_port,
            "session.set_mode",
            {
                "sessionId": session_id,
                "mode": mode,
            },
        )
        return bool(result.get("ok", False))

    async def set_model(self, local_port: int, session_id: str, model: str | None = None) -> bool:
        """Set the model for a session."""
        result = await self._rpc_call(
            local_port,
            "session.set_model",
            {
                "sessionId": session_id,
                "model": model,
            },
        )
        return bool(result.get("ok", False))

    async def health_check(self, local_port: int) -> dict[str, Any]:
        """Check daemon health."""
        return await self._rpc_call(local_port, "health.check")

    async def monitor_sessions(self, local_port: int) -> dict[str, Any]:
        """Get detailed monitoring info for all sessions."""
        return await self._rpc_call(local_port, "monitor.sessions")

    async def reconnect_session(self, local_port: int, session_id: str) -> list[dict[str, Any]]:
        """Reconnect to a session and get buffered events."""
        result = await self._rpc_call(
            local_port,
            "session.reconnect",
            {
                "sessionId": session_id,
            },
        )
        return result.get("bufferedEvents", [])

    async def get_queue_stats(self, local_port: int, session_id: str) -> dict[str, Any]:
        """Get message queue stats for a session."""
        return await self._rpc_call(
            local_port,
            "session.queue_stats",
            {
                "sessionId": session_id,
            },
        )

    async def interrupt_session(self, local_port: int, session_id: str) -> dict[str, Any]:
        """Interrupt the current Claude operation for a session.

        Sends SIGINT to the Claude CLI process to stop the current request.
        The process stays alive for future messages.

        Returns:
            dict with 'ok' and 'interrupted' (whether there was an active operation to interrupt).
        """
        return await self._rpc_call(
            local_port,
            "session.interrupt",
            {
                "sessionId": session_id,
            },
        )

    # ─── Cleanup ───

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


class DaemonError(Exception):
    """Error returned by the daemon RPC."""

    def __init__(self, message: str, code: int = -1):
        super().__init__(message)
        self.code = code


class DaemonConnectionError(Exception):
    """Cannot connect to daemon."""

    pass
