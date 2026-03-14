"""
Tests for head/message_formatter.py
"""

import pytest
from head.message_formatter import (
    display_mode,
    split_message,
    format_tool_use,
    format_session_info,
    format_machine_list,
    format_session_list,
    format_error,
    format_status,
    format_health,
    format_monitor,
    _truncate,
)
from head.session_router import Session


# ─── display_mode ───


class TestDisplayMode:
    def test_auto_maps_to_bypass(self):
        assert display_mode("auto") == "bypass"

    def test_code_passthrough(self):
        assert display_mode("code") == "code"

    def test_plan_passthrough(self):
        assert display_mode("plan") == "plan"

    def test_ask_passthrough(self):
        assert display_mode("ask") == "ask"

    def test_unknown_mode_passthrough(self):
        assert display_mode("custom") == "custom"

    def test_empty_string_passthrough(self):
        assert display_mode("") == ""


# ─── split_message ───


class TestSplitMessage:
    def test_short_text_single_chunk(self):
        text = "Hello, world!"
        result = split_message(text, max_len=2000)
        assert result == [text]

    def test_exact_max_len(self):
        text = "a" * 2000
        result = split_message(text, max_len=2000)
        assert result == [text]

    def test_long_text_splits(self):
        text = "Hello world. " * 200  # ~2600 chars
        result = split_message(text, max_len=100)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 100

    def test_splits_at_paragraph_boundary(self):
        para1 = "A" * 60
        para2 = "B" * 60
        text = para1 + "\n\n" + para2
        result = split_message(text, max_len=80)
        assert len(result) == 2
        assert result[0] == para1
        assert result[1] == para2

    def test_splits_at_line_boundary(self):
        line1 = "A" * 60
        line2 = "B" * 60
        text = line1 + "\n" + line2
        result = split_message(text, max_len=80)
        assert len(result) == 2
        assert result[0] == line1
        assert result[1] == line2

    def test_respects_code_blocks(self):
        # Build text with a code block that would straddle the split point
        before = "X" * 50
        code = "```\nsome code here\nmore code\n```"
        after = "Y" * 50
        text = before + "\n\n" + code + "\n\n" + after
        result = split_message(text, max_len=80)
        # Code block should NOT be split in the middle
        for chunk in result:
            count = chunk.count("```")
            assert count % 2 == 0, f"Chunk has unmatched code fences: {chunk!r}"

    def test_empty_string(self):
        result = split_message("", max_len=100)
        # Empty string yields empty list because of the strip filter
        assert result == [] or result == [""]

    def test_whitespace_only_returns_as_is(self):
        # Whitespace-only text under max_len is returned as a single chunk
        # (the short-circuit path returns before the strip filter)
        result = split_message("   \n\n   ", max_len=100)
        assert result == ["   \n\n   "]

    def test_force_split_no_boundaries(self):
        # Long text with no word/line boundaries
        text = "a" * 300
        result = split_message(text, max_len=100)
        assert len(result) > 1
        # All chunks fit within limit
        for chunk in result:
            assert len(chunk) <= 100

    def test_sentence_boundary_split(self):
        # Sentences that force a sentence-boundary split
        text = "A" * 55 + ". " + "B" * 55
        result = split_message(text, max_len=80)
        assert len(result) == 2

    def test_space_boundary_split(self):
        # Words that force a space-boundary split
        text = "word " * 30  # 150 chars
        result = split_message(text, max_len=80)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 80


# ─── format_tool_use ───


class TestFormatToolUse:
    def test_with_message(self):
        event = {"tool": "Bash", "message": "Running tests"}
        result = format_tool_use(event)
        assert "**[Tool: Bash]**" in result
        assert "Running tests" in result

    def test_with_input(self):
        event = {"tool": "Read", "input": {"path": "/foo/bar.py"}}
        result = format_tool_use(event)
        assert "**[Tool: Read]**" in result
        assert "```" in result
        assert "/foo/bar.py" in result

    def test_neither_message_nor_input(self):
        event = {"tool": "Write"}
        result = format_tool_use(event)
        assert result == "**[Tool: Write]**"

    def test_missing_tool(self):
        event = {}
        result = format_tool_use(event)
        assert "unknown" in result

    def test_message_takes_precedence_over_input(self):
        event = {"tool": "Bash", "message": "Running tests", "input": {"cmd": "ls"}}
        result = format_tool_use(event)
        assert "Running tests" in result
        assert "```" not in result  # input format not used when message present

    def test_long_input_truncated(self):
        event = {"tool": "Bash", "input": "x" * 600}
        result = format_tool_use(event)
        assert "..." in result


# ─── format_session_info ───


class TestFormatSessionInfo:
    def _make_session(self, **kwargs):
        defaults = {
            "channel_id": "discord:123",
            "machine_id": "gpu-1",
            "path": "/home/user/project",
            "daemon_session_id": "abcdef1234567890",
            "sdk_session_id": None,
            "status": "active",
            "mode": "auto",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        defaults.update(kwargs)
        return Session(**defaults)

    def test_session_object_active(self):
        session = self._make_session(status="active")
        result = format_session_info(session)
        assert "●" in result
        assert "abcdef12..." in result
        assert "gpu-1" in result
        assert "/home/user/project" in result
        assert "bypass" in result  # auto -> bypass
        assert "active" in result

    def test_session_object_detached(self):
        session = self._make_session(status="detached")
        result = format_session_info(session)
        assert "○" in result

    def test_session_object_destroyed(self):
        session = self._make_session(status="destroyed")
        result = format_session_info(session)
        assert "✕" in result

    def test_session_dict(self):
        session_dict = {
            "sessionId": "abcdef1234567890",
            "path": "/home/user/project",
            "mode": "code",
            "model": "claude-3-opus",
            "status": "idle",
        }
        result = format_session_info(session_dict)
        # Dict has no attributes, so getattr(session, "status", "") returns ""
        # which maps to "?" in the icon dict
        assert "?" in result
        assert "abcdef12..." in result
        assert "/home/user/project" in result
        assert "code" in result
        assert "claude-3-opus" in result

    def test_session_dict_no_model(self):
        session_dict = {
            "sessionId": "abcdef1234567890",
            "path": "/home/user/project",
            "mode": "auto",
            "status": "busy",
        }
        result = format_session_info(session_dict)
        # Dict uses "?" icon because getattr doesn't work on dicts
        assert "?" in result
        assert "bypass" in result  # auto -> bypass

    def test_session_dict_error_status(self):
        session_dict = {
            "sessionId": "abcdef1234567890",
            "path": "/test",
            "mode": "ask",
            "status": "error",
        }
        result = format_session_info(session_dict)
        # Dict uses "?" icon because getattr doesn't work on dicts
        assert "?" in result

    def test_session_dict_unknown_status(self):
        session_dict = {
            "sessionId": "abcdef1234567890",
            "path": "/test",
            "mode": "ask",
            "status": "weird",
        }
        result = format_session_info(session_dict)
        assert "?" in result

    def test_mode_display_in_session_object(self):
        session = self._make_session(mode="code")
        result = format_session_info(session)
        assert "code" in result

    def test_mode_display_auto_bypass_in_dict(self):
        session_dict = {
            "sessionId": "abcdef1234567890",
            "path": "/test",
            "mode": "auto",
            "status": "idle",
        }
        result = format_session_info(session_dict)
        assert "bypass" in result


# ─── format_machine_list ───


class TestFormatMachineList:
    def test_empty_list(self):
        result = format_machine_list([])
        assert result == "No machines configured."

    def test_single_online_machine(self):
        machines = [{
            "id": "gpu-1",
            "host": "192.168.1.100",
            "status": "online",
            "daemon": "running",
        }]
        result = format_machine_list(machines)
        assert "🟢" in result
        assert "⚡" in result
        assert "gpu-1" in result
        assert "192.168.1.100" in result

    def test_offline_machine(self):
        machines = [{
            "id": "gpu-2",
            "host": "192.168.1.101",
            "status": "offline",
            "daemon": "stopped",
        }]
        result = format_machine_list(machines)
        assert "🔴" in result
        assert "💤" in result

    def test_machine_with_paths(self):
        machines = [{
            "id": "gpu-1",
            "host": "192.168.1.100",
            "status": "online",
            "daemon": "running",
            "default_paths": ["/home/user/project1", "/home/user/project2"],
        }]
        result = format_machine_list(machines)
        assert "Paths:" in result
        assert "`/home/user/project1`" in result
        assert "`/home/user/project2`" in result

    def test_multiple_machines(self):
        machines = [
            {"id": "gpu-1", "host": "10.0.0.1", "status": "online", "daemon": "running"},
            {"id": "gpu-2", "host": "10.0.0.2", "status": "offline", "daemon": "stopped"},
        ]
        result = format_machine_list(machines)
        assert "gpu-1" in result
        assert "gpu-2" in result
        assert "Machines:" in result


# ─── format_session_list ───


class TestFormatSessionList:
    def _make_session(self, **kwargs):
        defaults = {
            "channel_id": "discord:123",
            "machine_id": "gpu-1",
            "path": "/home/user/project",
            "daemon_session_id": "abcdef1234567890",
            "sdk_session_id": None,
            "status": "active",
            "mode": "auto",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        defaults.update(kwargs)
        return Session(**defaults)

    def test_empty_list(self):
        result = format_session_list([])
        assert result == "No sessions found."

    def test_single_session(self):
        sessions = [self._make_session()]
        result = format_session_list(sessions)
        assert "Sessions:" in result
        assert "gpu-1" in result

    def test_multiple_sessions(self):
        sessions = [
            self._make_session(daemon_session_id="aaaa1234567890ab", machine_id="gpu-1"),
            self._make_session(daemon_session_id="bbbb1234567890ab", machine_id="gpu-2"),
        ]
        result = format_session_list(sessions)
        assert "Sessions:" in result
        assert "gpu-1" in result
        assert "gpu-2" in result


# ─── format_error ───


class TestFormatError:
    def test_basic(self):
        result = format_error("something went wrong")
        assert result == "**Error:** something went wrong"

    def test_empty_string(self):
        result = format_error("")
        assert result == "**Error:** "


# ─── format_status ───


class TestFormatStatus:
    def _make_session(self, **kwargs):
        defaults = {
            "channel_id": "discord:123",
            "machine_id": "gpu-1",
            "path": "/home/user/project",
            "daemon_session_id": "abcdef1234567890abcdef",
            "sdk_session_id": None,
            "status": "active",
            "mode": "auto",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        defaults.update(kwargs)
        return Session(**defaults)

    def test_basic_status(self):
        session = self._make_session()
        result = format_status(session)
        assert "Session Status" in result
        assert "gpu-1" in result
        assert "/home/user/project" in result
        assert "bypass" in result  # auto -> bypass
        assert "active" in result
        assert "abcdef123456..." in result

    def test_mode_display(self):
        session = self._make_session(mode="code")
        result = format_status(session)
        assert "code" in result

    def test_with_sdk_session(self):
        session = self._make_session(sdk_session_id="sdk-session-1234567890ab")
        result = format_status(session)
        assert "SDK Session:" in result
        # sdk_session_id[:12] = "sdk-session-" then "..." appended
        assert "sdk-session-..." in result

    def test_without_sdk_session(self):
        session = self._make_session(sdk_session_id=None)
        result = format_status(session)
        assert "SDK Session:" not in result

    def test_with_queue_stats(self):
        session = self._make_session()
        queue_stats = {"userPending": 3, "responsePending": 1}
        result = format_status(session, queue_stats)
        assert "3 pending messages" in result
        assert "1 responses" in result

    def test_without_queue_stats(self):
        session = self._make_session()
        result = format_status(session, None)
        assert "pending messages" not in result


# ─── format_health ───


class TestFormatHealth:
    def test_full_health(self):
        health = {
            "ok": True,
            "uptime": 7265,  # 2h01m05s
            "sessions": 3,
            "sessionsByStatus": {"idle": 2, "busy": 1},
            "memory": {"rss": 128, "heapUsed": 64, "heapTotal": 128},
            "nodeVersion": "v18.17.0",
            "pid": 12345,
        }
        result = format_health("gpu-1", health)
        assert "Daemon Health - gpu-1" in result
        assert "OK" in result
        assert "2h01m05s" in result
        assert "3" in result
        assert "idle: 2" in result
        assert "busy: 1" in result
        assert "128MB RSS" in result
        assert "64/128MB heap" in result
        assert "v18.17.0" in result
        assert "12345" in result

    def test_uptime_hours(self):
        health = {"ok": True, "uptime": 3661, "sessions": 0, "memory": {}, "sessionsByStatus": {}}
        result = format_health("m1", health)
        assert "1h01m01s" in result

    def test_uptime_minutes(self):
        health = {"ok": True, "uptime": 125, "sessions": 0, "memory": {}, "sessionsByStatus": {}}
        result = format_health("m1", health)
        assert "2m05s" in result

    def test_uptime_seconds_only(self):
        health = {"ok": True, "uptime": 42, "sessions": 0, "memory": {}, "sessionsByStatus": {}}
        result = format_health("m1", health)
        assert "42s" in result
        assert "m" not in result.split("42s")[0].split("Uptime:")[1]

    def test_health_not_ok(self):
        health = {"ok": False, "uptime": 0, "sessions": 0, "memory": {}, "sessionsByStatus": {}}
        result = format_health("m1", health)
        assert "ERROR" in result

    def test_no_sessions_by_status(self):
        health = {"ok": True, "uptime": 0, "sessions": 0, "sessionsByStatus": {}, "memory": {}}
        result = format_health("m1", health)
        assert "none" in result

    def test_memory_missing_fields(self):
        health = {"ok": True, "uptime": 0, "sessions": 0, "sessionsByStatus": {}, "memory": {}}
        result = format_health("m1", health)
        assert "?MB RSS" in result


# ─── format_monitor ───


class TestFormatMonitor:
    def test_no_sessions(self):
        monitor = {"sessions": [], "uptime": 100}
        result = format_monitor("gpu-1", monitor)
        assert "No active sessions" in result
        assert "gpu-1" in result

    def test_single_session(self):
        monitor = {
            "uptime": 3665,
            "sessions": [{
                "sessionId": "abcdef1234567890",
                "status": "idle",
                "mode": "auto",
                "model": "claude-3-opus",
                "path": "/home/user/project",
                "queue": {
                    "userPending": 0,
                    "responsePending": 0,
                    "clientConnected": True,
                },
            }],
        }
        result = format_monitor("gpu-1", monitor)
        assert "Monitor - gpu-1" in result
        assert "1h01m05s" in result
        assert "1 session(s)" in result
        assert "●" in result
        assert "idle" in result
        assert "bypass" in result  # auto -> bypass
        assert "claude-3-opus" in result
        assert "/home/user/project" in result
        assert "connected" in result

    def test_multiple_sessions(self):
        monitor = {
            "uptime": 60,
            "sessions": [
                {
                    "sessionId": "aaaa1234567890ab",
                    "status": "idle",
                    "mode": "code",
                    "path": "/project1",
                    "queue": {"userPending": 0, "responsePending": 0, "clientConnected": True},
                },
                {
                    "sessionId": "bbbb1234567890ab",
                    "status": "busy",
                    "mode": "auto",
                    "path": "/project2",
                    "queue": {"userPending": 2, "responsePending": 1, "clientConnected": False},
                },
            ],
        }
        result = format_monitor("gpu-1", monitor)
        assert "2 session(s)" in result
        assert "●" in result  # idle
        assert "◉" in result  # busy
        assert "code" in result
        assert "bypass" in result
        assert "**disconnected**" in result
        assert "2 pending" in result
        assert "1 buffered" in result

    def test_error_status_session(self):
        monitor = {
            "uptime": 10,
            "sessions": [{
                "sessionId": "cccc1234567890ab",
                "status": "error",
                "mode": "ask",
                "path": "/err",
                "queue": {"userPending": 0, "responsePending": 0, "clientConnected": False},
            }],
        }
        result = format_monitor("gpu-1", monitor)
        assert "✕" in result

    def test_session_no_model(self):
        monitor = {
            "uptime": 0,
            "sessions": [{
                "sessionId": "dddd1234567890ab",
                "status": "idle",
                "mode": "plan",
                "path": "/test",
                "queue": {"userPending": 0, "responsePending": 0, "clientConnected": True},
            }],
        }
        result = format_monitor("gpu-1", monitor)
        assert "plan" in result
        # No model should be in the output
        assert "| ]" not in result  # ensure no empty model field

    def test_uptime_formats(self):
        # Seconds only
        result = format_monitor("m1", {"sessions": [{"sessionId": "aaaa1234567890ab", "status": "idle", "mode": "auto", "path": "/t", "queue": {"userPending": 0, "responsePending": 0, "clientConnected": True}}], "uptime": 30})
        assert "30s" in result

        # Minutes and seconds
        result = format_monitor("m1", {"sessions": [{"sessionId": "aaaa1234567890ab", "status": "idle", "mode": "auto", "path": "/t", "queue": {"userPending": 0, "responsePending": 0, "clientConnected": True}}], "uptime": 125})
        assert "2m05s" in result


# ─── _truncate ───


class TestTruncate:
    def test_short_text(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length(self):
        assert _truncate("hello", 5) == "hello"

    def test_long_text(self):
        result = _truncate("hello world", 8)
        assert result == "hello..."
        assert len(result) == 8
