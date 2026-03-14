"""
Message Formatter - handles splitting long messages for Discord/Telegram
and formatting various Claude output types.
"""

import re
from typing import Any


# Display names for permission modes (user-facing)
MODE_DISPLAY_NAMES = {
    "auto": "bypass",
    "code": "code",
    "plan": "plan",
    "ask": "ask",
}


def display_mode(mode: str) -> str:
    """Convert internal mode name to display name."""
    return MODE_DISPLAY_NAMES.get(mode, mode)


def split_message(text: str, max_len: int = 2000) -> list[str]:
    """
    Split a long message into chunks that fit within platform limits.
    Smart splitting: avoids breaking code blocks, prefers paragraph boundaries.

    Args:
        text: The text to split
        max_len: Maximum length per chunk (Discord=2000, Telegram=4096)

    Returns:
        List of message chunks
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Find the best split point within max_len
        split_at = _find_split_point(remaining, max_len)
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")

    return [c for c in chunks if c.strip()]


def _find_split_point(text: str, max_len: int) -> int:
    """Find the best point to split text at, within max_len."""
    segment = text[:max_len]

    # Check if we're inside a code block
    code_blocks = list(re.finditer(r'```', segment))
    if len(code_blocks) % 2 == 1:
        # Odd number of ``` means we're inside a code block
        # Find the last complete code block end before max_len
        last_block_start = code_blocks[-1].start()
        if last_block_start > 200:  # Don't split too early
            return last_block_start

    # Try to split at paragraph boundary (\n\n)
    last_para = segment.rfind("\n\n")
    if last_para > max_len * 0.3:  # At least 30% of the way through
        return last_para + 1

    # Try to split at line boundary (\n)
    last_line = segment.rfind("\n")
    if last_line > max_len * 0.3:
        return last_line + 1

    # Try to split at sentence boundary
    for pattern in [". ", "! ", "? ", "; "]:
        last_sentence = segment.rfind(pattern)
        if last_sentence > max_len * 0.5:
            return last_sentence + 2

    # Try space
    last_space = segment.rfind(" ")
    if last_space > max_len * 0.5:
        return last_space + 1

    # Force split at max_len
    return max_len


def format_tool_use(event: dict[str, Any]) -> str:
    """Format a tool_use event for display in chat."""
    tool = event.get("tool", "unknown")
    input_data = event.get("input")
    message = event.get("message", "")

    if message:
        return f"**[Tool: {tool}]** {message}"

    if input_data:
        input_str = _truncate(str(input_data), 500)
        return f"**[Tool: {tool}]**\n```\n{input_str}\n```"

    return f"**[Tool: {tool}]**"


def format_session_info(session: Any) -> str:
    """Format a session for display."""
    status_icon = {
        "active": "●",
        "detached": "○",
        "destroyed": "✕",
        "idle": "●",
        "busy": "◉",
        "error": "✕",
    }.get(getattr(session, "status", ""), "?")

    if hasattr(session, "channel_id"):
        # Session from SessionRouter
        mode_str = display_mode(session.mode)
        name_str = f" **{session.name}**" if session.name else ""
        return (
            f"{status_icon}{name_str} `{session.daemon_session_id}` "
            f"**{session.machine_id}**:`{session.path}` "
            f"[{mode_str}] ({session.status})"
        )
    else:
        # Session info dict from daemon
        sid = session.get("sessionId", "?")
        mode_str = display_mode(session.get("mode", "?"))
        model = session.get("model", "")
        model_str = f" | {model}" if model else ""
        return (
            f"{status_icon} `{sid}` "
            f"**{session.get('path', '?')}** "
            f"[{mode_str}{model_str}] ({session.get('status', '?')})"
        )


def format_machine_list(machines: list[dict[str, Any]]) -> str:
    """Format machine list for display."""
    if not machines:
        return "No machines configured."

    lines = ["**Machines:**"]
    for m in machines:
        status_icon = "🟢" if m.get("status") == "online" else "🔴"
        daemon_icon = "⚡" if m.get("daemon") == "running" else "💤"
        line = f"{status_icon} **{m['id']}** ({m['host']}) {daemon_icon}"
        if m.get("default_paths"):
            paths = ", ".join(f"`{p}`" for p in m["default_paths"])
            line += f"\n  Paths: {paths}"
        lines.append(line)

    return "\n".join(lines)


def format_session_list(sessions: list[Any]) -> str:
    """Format session list for display."""
    if not sessions:
        return "No sessions found."

    lines = ["**Sessions:**"]
    for s in sessions:
        lines.append(format_session_info(s))

    return "\n".join(lines)


def format_error(error: str) -> str:
    """Format an error message."""
    return f"**Error:** {error}"


def format_status(session: Any, queue_stats: dict[str, Any] | None = None) -> str:
    """Format session status for /status command."""
    mode_str = display_mode(session.mode)
    name_str = session.name if session.name else "(unnamed)"
    lines = [
        f"**Session Status**",
        f"Name: **{name_str}**",
        f"Machine: **{session.machine_id}**",
        f"Path: `{session.path}`",
        f"Mode: **{mode_str}**",
        f"Status: **{session.status}**",
        f"Session ID: `{session.daemon_session_id}`",
    ]

    if session.sdk_session_id:
        lines.append(f"SDK Session: `{session.sdk_session_id}`")

    if queue_stats:
        lines.append(f"Queue: {queue_stats.get('userPending', 0)} pending messages")
        lines.append(f"Buffered: {queue_stats.get('responsePending', 0)} responses")

    return "\n".join(lines)


def format_health(machine_id: str, health: dict[str, Any]) -> str:
    """Format daemon health check result."""
    uptime_secs = health.get("uptime", 0)
    hours, remainder = divmod(uptime_secs, 3600)
    mins, secs = divmod(remainder, 60)
    if hours > 0:
        uptime_str = f"{hours}h{mins:02d}m{secs:02d}s"
    elif mins > 0:
        uptime_str = f"{mins}m{secs:02d}s"
    else:
        uptime_str = f"{secs}s"

    status_counts = health.get("sessionsByStatus", {})
    status_parts = [f"{k}: {v}" for k, v in status_counts.items()] if status_counts else ["none"]

    memory = health.get("memory", {})
    mem_str = f"{memory.get('rss', '?')}MB RSS, {memory.get('heapUsed', '?')}/{memory.get('heapTotal', '?')}MB heap"

    lines = [
        f"**Daemon Health - {machine_id}**",
        f"Status: {'OK' if health.get('ok') else 'ERROR'}",
        f"Uptime: {uptime_str}",
        f"Sessions: {health.get('sessions', 0)} ({', '.join(status_parts)})",
        f"Memory: {mem_str}",
        f"Node: {health.get('nodeVersion', '?')} (PID: {health.get('pid', '?')})",
    ]
    return "\n".join(lines)


def format_monitor(machine_id: str, monitor: dict[str, Any]) -> str:
    """Format monitor.sessions result."""
    sessions = monitor.get("sessions", [])
    if not sessions:
        return f"**Monitor - {machine_id}**: No active sessions."

    uptime_secs = monitor.get("uptime", 0)
    hours, remainder = divmod(uptime_secs, 3600)
    mins, secs = divmod(remainder, 60)
    if hours > 0:
        uptime_str = f"{hours}h{mins:02d}m{secs:02d}s"
    elif mins > 0:
        uptime_str = f"{mins}m{secs:02d}s"
    else:
        uptime_str = f"{secs}s"

    lines = [
        f"**Monitor - {machine_id}** (uptime: {uptime_str}, {len(sessions)} session(s))",
        "",
    ]

    for s in sessions:
        sid = s.get("sessionId", "?")
        status = s.get("status", "?")
        mode_str = display_mode(s.get("mode", "?"))
        model = s.get("model", "")
        path = s.get("path", "?")
        queue = s.get("queue", {})
        user_pending = queue.get("userPending", 0)
        resp_pending = queue.get("responsePending", 0)
        connected = queue.get("clientConnected", False)

        status_icon = {
            "idle": "●", "busy": "◉", "error": "✕", "destroyed": "✕"
        }.get(status, "?")

        conn_icon = "connected" if connected else "**disconnected**"
        model_str = f" | {model}" if model else ""

        lines.append(f"{status_icon} `{sid}` **{status}** [{mode_str}{model_str}]")
        lines.append(f"  Path: `{path}`")
        lines.append(f"  Client: {conn_icon} | Queue: {user_pending} pending, {resp_pending} buffered")
        lines.append("")

    return "\n".join(lines).rstrip()


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
