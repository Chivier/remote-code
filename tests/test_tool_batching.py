"""
Tests for tool_use message batching (compress_tool_messages + _forward_message batching).

Verifies:
1. compress_tool_messages() correctly formats batched tool events
2. _forward_message() only batches tool_use events — all other event types
   (partial, text, result, system, queued, error) are sent individually and
   immediately, with no batching or compression applied to them.
3. tool batches are flushed at the right boundaries (batch full, non-tool event,
   stream end).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Any, Optional

from head.engine import BotEngine
from head.platform.protocol import MessageHandle, InputHandler
from head.config import Config, MachineConfig
from head.session_router import SessionRouter
from head.message_formatter import (
    compress_tool_messages,
    format_tool_use,
)
from pathlib import Path


# ─── MockAdapter + MockBotEngine for testing ───


class MockAdapter:
    """Mock PlatformAdapter that records sent/edited messages."""

    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []
        self.edited_messages: list[tuple[str, str]] = []
        self._msg_counter = 0
        self._on_input: Optional[InputHandler] = None

    @property
    def platform_name(self) -> str:
        return "discord"

    @property
    def max_message_length(self) -> int:
        return 2000

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        self._msg_counter += 1
        self.sent_messages.append((channel_id, text))
        return MessageHandle(platform="discord", channel_id=channel_id, message_id=f"msg-{self._msg_counter}")

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        self.edited_messages.append((handle.message_id, text))

    async def delete_message(self, handle: MessageHandle) -> None:
        pass

    async def download_file(self, attachment, dest: Path) -> Path:
        return dest

    async def send_file(self, channel_id: str, path: Path, caption: str = "") -> MessageHandle:
        return MessageHandle(platform="discord", channel_id=channel_id, message_id="file-1")

    async def start_typing(self, channel_id: str) -> None:
        pass

    async def stop_typing(self, channel_id: str) -> None:
        pass

    def supports_message_edit(self) -> bool:
        return True

    def supports_inline_buttons(self) -> bool:
        return False

    def supports_file_upload(self) -> bool:
        return True

    def set_input_handler(self, handler: InputHandler) -> None:
        self._on_input = handler

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class MockBotEngine(BotEngine):
    """BotEngine with convenience test accessors."""

    def __init__(self, adapter, ssh_manager, session_router, daemon_client, config):
        super().__init__(adapter, ssh_manager, session_router, daemon_client, config)
        self.adapter: MockAdapter

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        return self.adapter.sent_messages

    @property
    def edited_messages(self) -> list[tuple[str, str]]:
        return self.adapter.edited_messages

    def get_all_texts(self) -> list[str]:
        return [text for _, text in self.adapter.sent_messages]


# ─── Fixtures ───


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    ssh.ensure_tunnel = AsyncMock(return_value=19100)
    ssh.get_local_port = MagicMock(return_value=None)
    ssh.sync_skills = AsyncMock()
    ssh.list_machines = AsyncMock(return_value=[])
    return ssh


@pytest.fixture
def mock_router(tmp_path):
    return SessionRouter(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def mock_daemon():
    return AsyncMock()


@pytest.fixture
def make_config():
    """Factory that creates a Config with a given tool_batch_size."""
    def _make(tool_batch_size: int = 15):
        config = Config()
        config.machines = {
            "gpu-1": MachineConfig(id="gpu-1", host="10.0.0.1", user="user"),
        }
        config.default_mode = "auto"
        config.tool_batch_size = tool_batch_size
        return config
    return _make


@pytest.fixture
def make_bot(mock_ssh, mock_router, mock_daemon, make_config):
    """Factory that creates a MockBotEngine with a given tool_batch_size."""
    def _make(tool_batch_size: int = 15):
        config = make_config(tool_batch_size)
        adapter = MockAdapter()
        engine = MockBotEngine(adapter, mock_ssh, mock_router, mock_daemon, config)
        adapter.set_input_handler(engine.handle_input)
        return engine
    return _make


def _tool_event(tool: str, message: str = "", input_data: Any = None) -> dict:
    e: dict = {"type": "tool_use", "tool": tool}
    if message:
        e["message"] = message
    if input_data is not None:
        e["input"] = input_data
    return e


# ═══════════════════════════════════════════════════════════
# Part 1: compress_tool_messages unit tests
# ═══════════════════════════════════════════════════════════


class TestCompressToolMessages:
    def test_empty_list_returns_empty_string(self):
        assert compress_tool_messages([]) == ""

    def test_single_event_delegates_to_format_tool_use(self):
        event = {"tool": "Bash", "message": "Running tests"}
        assert compress_tool_messages([event]) == format_tool_use(event)

    def test_single_event_with_input(self):
        event = {"tool": "Read", "input": {"path": "/foo/bar.py"}}
        assert compress_tool_messages([event]) == format_tool_use(event)

    def test_two_events_shows_header_with_count(self):
        events = [
            {"tool": "Read", "message": "file1.py"},
            {"tool": "Edit", "message": "file2.py"},
        ]
        result = compress_tool_messages(events)
        assert result.startswith("**[Tools: 2 calls]**")

    def test_multiple_events_lists_all_tools(self):
        events = [
            {"tool": "Read", "message": "file1.py"},
            {"tool": "Edit", "message": "file2.py"},
            {"tool": "Bash", "message": "npm test"},
        ]
        result = compress_tool_messages(events)
        assert "**[Tools: 3 calls]**" in result
        assert "`Read`" in result
        assert "`Edit`" in result
        assert "`Bash`" in result
        assert "file1.py" in result
        assert "file2.py" in result
        assert "npm test" in result

    def test_event_with_input_shows_truncated_input(self):
        events = [
            {"tool": "Read"},
            {"tool": "Edit", "input": {"path": "/very/long/path.py"}},
        ]
        result = compress_tool_messages(events)
        assert "`Edit`" in result
        assert "/very/long/path.py" in result

    def test_event_with_no_message_or_input(self):
        events = [
            {"tool": "Read"},
            {"tool": "Write"},
        ]
        result = compress_tool_messages(events)
        assert "`Read`" in result
        assert "`Write`" in result

    def test_missing_tool_key_defaults_to_unknown(self):
        events = [
            {},
            {"tool": "Bash"},
        ]
        result = compress_tool_messages(events)
        assert "`unknown`" in result
        assert "`Bash`" in result

    def test_long_message_truncated_at_120(self):
        long_msg = "x" * 200
        events = [
            {"tool": "Bash", "message": long_msg},
            {"tool": "Read"},
        ]
        result = compress_tool_messages(events)
        # The truncated line for Bash should not exceed 120 chars of detail
        lines = result.split("\n")
        bash_line = [l for l in lines if "Bash" in l][0]
        # After "  `Bash` — " prefix, the detail part should be truncated
        assert "..." in bash_line

    def test_message_takes_precedence_over_input(self):
        events = [
            {"tool": "Bash", "message": "Running", "input": {"cmd": "ls"}},
            {"tool": "Read"},
        ]
        result = compress_tool_messages(events)
        assert "Running" in result

    def test_fifteen_events(self):
        events = [{"tool": f"Tool{i}", "message": f"action{i}"} for i in range(15)]
        result = compress_tool_messages(events)
        assert "**[Tools: 15 calls]**" in result
        for i in range(15):
            assert f"`Tool{i}`" in result


# ═══════════════════════════════════════════════════════════
# Part 2: _forward_message batching integration tests
# ═══════════════════════════════════════════════════════════


class TestForwardMessageBatching:
    """
    Test that _forward_message correctly batches tool_use events
    and sends all other event types individually.
    """

    @pytest.mark.asyncio
    async def test_single_tool_event_sent_individually(self, make_bot, mock_router, mock_daemon):
        """A single tool_use event (below batch size) is flushed at stream end."""
        bot = make_bot(tool_batch_size=3)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="file.py")
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_texts = [t for t in texts if "[Tool:" in t]
        assert len(tool_texts) == 1
        # Single event uses format_tool_use (not compressed header)
        assert "**[Tool: Read]**" in tool_texts[0]
        assert "**[Tools:" not in tool_texts[0]

    @pytest.mark.asyncio
    async def test_batch_flushes_at_batch_size(self, make_bot, mock_router, mock_daemon):
        """Exactly batch_size tool events produce one compressed message."""
        bot = make_bot(tool_batch_size=3)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield _tool_event("Edit", message="f2")
            yield _tool_event("Bash", message="test")
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_texts = [t for t in texts if "Tool" in t]
        assert len(tool_texts) == 1
        assert "**[Tools: 3 calls]**" in tool_texts[0]

    @pytest.mark.asyncio
    async def test_batch_flushes_before_text_event(self, make_bot, mock_router, mock_daemon):
        """Accumulated tool events flush when a text event arrives."""
        bot = make_bot(tool_batch_size=15)  # Large batch — won't fill up
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield _tool_event("Edit", message="f2")
            yield {"type": "text", "content": "Done editing."}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        # Tool batch flushed before text, then text sent
        tool_texts = [t for t in texts if "Tools:" in t]
        text_texts = [t for t in texts if "Done editing." in t]
        assert len(tool_texts) == 1
        assert len(text_texts) == 1
        # Tool message must appear before text message
        tool_idx = texts.index(tool_texts[0])
        text_idx = texts.index(text_texts[0])
        assert tool_idx < text_idx

    @pytest.mark.asyncio
    async def test_text_events_never_batched(self, make_bot, mock_router, mock_daemon):
        """Multiple text events are each sent as individual messages."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "text", "content": "First paragraph."}
            yield {"type": "text", "content": "Second paragraph."}
            yield {"type": "text", "content": "Third paragraph."}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        assert "First paragraph." in texts
        assert "Second paragraph." in texts
        assert "Third paragraph." in texts

    @pytest.mark.asyncio
    async def test_error_events_never_batched(self, make_bot, mock_router, mock_daemon):
        """Each error event produces its own message immediately."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "error", "message": "err1"}
            yield {"type": "error", "message": "err2"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        error_texts = [t for t in texts if "Error" in t]
        assert len(error_texts) == 2
        assert any("err1" in t for t in error_texts)
        assert any("err2" in t for t in error_texts)

    @pytest.mark.asyncio
    async def test_system_events_never_batched(self, make_bot, mock_router, mock_daemon):
        """System init events produce individual messages."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "system", "subtype": "init", "model": "claude-opus"}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        assert any("claude-opus" in t for t in texts)

    @pytest.mark.asyncio
    async def test_queued_event_never_batched(self, make_bot, mock_router, mock_daemon):
        """Queued event produces its own message."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "queued", "position": 2}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        assert any("queued" in t.lower() for t in texts)
        assert any("2" in t for t in texts)

    @pytest.mark.asyncio
    async def test_tool_batch_flushed_before_error(self, make_bot, mock_router, mock_daemon):
        """Tool batch is flushed before an error event is sent."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield _tool_event("Edit", message="f2")
            yield {"type": "error", "message": "something broke"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_idx = next(i for i, t in enumerate(texts) if "Tool" in t)
        err_idx = next(i for i, t in enumerate(texts) if "broke" in t)
        assert tool_idx < err_idx

    @pytest.mark.asyncio
    async def test_tool_batch_flushed_before_queued(self, make_bot, mock_router, mock_daemon):
        """Tool batch is flushed before a queued event."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield {"type": "queued", "position": 1}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_idx = next(i for i, t in enumerate(texts) if "Tool" in t)
        queued_idx = next(i for i, t in enumerate(texts) if "queued" in t.lower())
        assert tool_idx < queued_idx

    @pytest.mark.asyncio
    async def test_tool_batch_flushed_before_system(self, make_bot, mock_router, mock_daemon):
        """Tool batch is flushed before a system event."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Bash", message="npm install")
            yield {"type": "system", "subtype": "init", "model": "claude-opus"}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_idx = next(i for i, t in enumerate(texts) if "Tool" in t)
        sys_idx = next(i for i, t in enumerate(texts) if "claude-opus" in t)
        assert tool_idx < sys_idx

    @pytest.mark.asyncio
    async def test_multiple_batches_when_exceeding_size(self, make_bot, mock_router, mock_daemon):
        """When more tool events than batch size arrive, multiple batches are sent."""
        bot = make_bot(tool_batch_size=3)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            for i in range(7):
                yield _tool_event(f"Tool{i}", message=f"action{i}")
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_texts = [t for t in texts if "Tool" in t]
        # 7 events with batch_size=3: batch of 3, batch of 3, remainder of 1
        assert len(tool_texts) == 3
        assert "**[Tools: 3 calls]**" in tool_texts[0]
        assert "**[Tools: 3 calls]**" in tool_texts[1]
        # Last one is a single event — uses format_tool_use
        assert "**[Tool: Tool6]**" in tool_texts[2]

    @pytest.mark.asyncio
    async def test_interleaved_tool_and_text(self, make_bot, mock_router, mock_daemon):
        """
        Tool-text-tool-text pattern: each tool group is flushed before its
        following text, and text events are sent individually.
        """
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="file1")
            yield _tool_event("Edit", message="file1")
            yield {"type": "text", "content": "Edited file1."}
            yield _tool_event("Read", message="file2")
            yield {"type": "text", "content": "Read file2."}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        # Expected order:
        # 1. compressed tool batch (Read+Edit)
        # 2. "Edited file1."
        # 3. single tool (Read file2) — flushed before "Read file2."
        # 4. "Read file2."
        assert "**[Tools: 2 calls]**" in texts[0]
        assert "Edited file1." in texts[1]
        assert "**[Tool: Read]**" in texts[2]
        assert "file2" in texts[2]
        assert "Read file2." in texts[3]

    @pytest.mark.asyncio
    async def test_batch_size_one_disables_batching(self, make_bot, mock_router, mock_daemon):
        """With tool_batch_size=1, every tool event is sent immediately (no compression)."""
        bot = make_bot(tool_batch_size=1)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield _tool_event("Edit", message="f2")
            yield _tool_event("Bash", message="test")
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_texts = [t for t in texts if "[Tool:" in t]
        assert len(tool_texts) == 3
        # Each is a single-event message (format_tool_use format)
        assert "**[Tool: Read]**" in tool_texts[0]
        assert "**[Tool: Edit]**" in tool_texts[1]
        assert "**[Tool: Bash]**" in tool_texts[2]

    @pytest.mark.asyncio
    async def test_result_event_not_sent_as_message(self, make_bot, mock_router, mock_daemon):
        """result events update session state but don't produce user-visible messages."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "result", "session_id": "sdk-999"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        # result event should not produce any sent message
        assert len(texts) == 0

    @pytest.mark.asyncio
    async def test_ping_events_ignored(self, make_bot, mock_router, mock_daemon):
        """ping events are silently ignored."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield {"type": "ping"}
            yield {"type": "ping"}
            yield {"type": "text", "content": "Hello"}
            yield {"type": "ping"}
            yield {"type": "result", "session_id": "sdk-1"}

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        assert texts == ["Hello"]

    @pytest.mark.asyncio
    async def test_remaining_tool_batch_flushed_at_stream_end(self, make_bot, mock_router, mock_daemon):
        """Tool events at the end of the stream (no subsequent non-tool event) are still flushed."""
        bot = make_bot(tool_batch_size=15)
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_stream(*a, **kw):
            yield _tool_event("Read", message="f1")
            yield _tool_event("Edit", message="f2")
            # Stream ends without a text/result event after tools

        mock_daemon.send_message = mock_stream
        await bot.handle_input("discord:100", "hello")

        texts = bot.get_all_texts()
        tool_texts = [t for t in texts if "Tool" in t]
        assert len(tool_texts) == 1
        assert "**[Tools: 2 calls]**" in tool_texts[0]


# ═══════════════════════════════════════════════════════════
# Part 3: Config tests
# ═══════════════════════════════════════════════════════════


class TestToolBatchConfig:
    def test_default_value(self):
        config = Config()
        assert config.tool_batch_size == 15

    def test_custom_value(self):
        config = Config()
        config.tool_batch_size = 5
        assert config.tool_batch_size == 5
