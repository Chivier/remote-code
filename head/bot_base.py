"""
Bot Base - abstract base class for Discord and Telegram bots.

Contains all the shared command handling and message forwarding logic.
Subclasses implement platform-specific send/edit operations.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from .config import Config
from .ssh_manager import SSHManager
from .session_router import SessionRouter
from .daemon_client import DaemonClient, DaemonError, DaemonConnectionError
from .message_formatter import (
    split_message,
    format_tool_use,
    compress_tool_messages,
    format_machine_list,
    format_session_list,
    format_error,
    format_status,
    format_health,
    format_monitor,
    display_mode,
)

logger = logging.getLogger(__name__)

# How often to update the "streaming" message (seconds)
STREAM_UPDATE_INTERVAL = 1.5
# Maximum buffer before forcing a message send
STREAM_BUFFER_FLUSH_SIZE = 1800


class BotBase(ABC):
    """
    Abstract base class for chat bot implementations.

    Handles command routing, session management, and message forwarding.
    Subclasses implement platform-specific message sending.
    """

    def __init__(
        self,
        ssh_manager: SSHManager,
        session_router: SessionRouter,
        daemon_client: DaemonClient,
        config: Config,
    ):
        self.ssh = ssh_manager
        self.router = session_router
        self.daemon = daemon_client
        self.config = config
        # Track which channels are currently streaming (to prevent concurrent sends)
        self._streaming: set[str] = set()

    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> Any:
        """Send a new message to the channel. Returns platform message object."""
        ...

    @abstractmethod
    async def edit_message(self, channel_id: str, message_obj: Any, text: str) -> None:
        """Edit an existing message."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start the bot (connect to platform, begin listening)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the bot."""
        ...

    # ─── Command Dispatcher ───

    async def handle_input(self, channel_id: str, text: str) -> None:
        """
        Main entry point: handle a user message from a chat channel.
        Routes to commands or forwards to Claude session.
        """
        text = text.strip()
        if not text:
            return

        # Check if it's a command
        if text.startswith("/"):
            await self._handle_command(channel_id, text)
        else:
            # Forward to active Claude session
            await self._forward_message(channel_id, text)

    async def _handle_command(self, channel_id: str, text: str) -> None:
        """Parse and dispatch a command."""
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        try:
            if cmd == "/start":
                await self.cmd_start(channel_id, args)
            elif cmd == "/resume":
                await self.cmd_resume(channel_id, args)
            elif cmd in ("/ls", "/list"):
                await self.cmd_ls(channel_id, args)
            elif cmd == "/exit":
                await self.cmd_exit(channel_id)
            elif cmd in ("/rm", "/remove", "/destroy"):
                await self.cmd_rm(channel_id, args)
            elif cmd == "/mode":
                await self.cmd_mode(channel_id, args)
            elif cmd == "/status":
                await self.cmd_status(channel_id)
            elif cmd == "/interrupt":
                await self.cmd_interrupt(channel_id)
            elif cmd == "/rename":
                await self.cmd_rename(channel_id, args)
            elif cmd == "/health":
                await self.cmd_health(channel_id, args)
            elif cmd == "/monitor":
                await self.cmd_monitor(channel_id, args)
            elif cmd == "/help":
                await self.cmd_help(channel_id)
            else:
                await self.send_message(channel_id, f"Unknown command: `{cmd}`. Use `/help` for available commands.")
        except DaemonConnectionError as e:
            await self.send_message(channel_id, format_error(f"Cannot connect to daemon: {e}"))
        except DaemonError as e:
            await self.send_message(channel_id, format_error(f"Daemon error: {e}"))
        except Exception as e:
            logger.exception(f"Error handling command: {text}")
            await self.send_message(channel_id, format_error(str(e)))

    # ─── Commands ───

    async def cmd_start(self, channel_id: str, args: list[str], silent_init: bool = False) -> None:
        """/start <machine> <path> - Create a new session."""
        if len(args) < 2:
            await self.send_message(
                channel_id,
                "Usage: `/start <machine_id> <path>`\nExample: `/start gpu-1 /home/user/project`"
            )
            return

        machine_id = args[0]
        path = args[1]

        if not silent_init:
            await self.send_message(channel_id, f"Starting session on **{machine_id}**:`{path}`...")

        # Ensure SSH tunnel
        local_port = await self.ssh.ensure_tunnel(machine_id)

        # Sync skills
        await self.ssh.sync_skills(machine_id, path)

        # Create session on daemon
        daemon_session_id = await self.daemon.create_session(
            local_port, path, self.config.default_mode
        )

        # Register in session router
        name = self.router.register(
            channel_id, machine_id, path, daemon_session_id, self.config.default_mode
        )

        await self.send_message(
            channel_id,
            f"Session started on **{machine_id}**:`{path}`\n"
            f"Name: **{name}**\n"
            f"Session ID: `{daemon_session_id}`\n"
            f"Mode: **{display_mode(self.config.default_mode)}**\n\n"
            f"Send messages to interact with Claude."
        )

    async def cmd_resume(self, channel_id: str, args: list[str]) -> None:
        """/resume <session_id_or_name> - Resume a previous session."""
        if len(args) < 1:
            await self.send_message(channel_id, "Usage: `/resume <session_id_or_name>`")
            return

        identifier = args[0]

        # Find the session by name or daemon ID
        session = self.router.find_session_by_name_or_id(identifier)
        if not session:
            await self.send_message(channel_id, f"Session `{identifier}` not found in records.")
            return

        session_id = session.daemon_session_id
        name_str = f" (**{session.name}**)" if session.name else ""

        await self.send_message(channel_id, f"Resuming session{name_str} on **{session.machine_id}**:`{session.path}`...")

        # Ensure tunnel
        local_port = await self.ssh.ensure_tunnel(session.machine_id)

        # Resume on daemon
        result = await self.daemon.resume_session(
            local_port, session_id, session.sdk_session_id
        )

        if not result.get("ok"):
            await self.send_message(channel_id, format_error("Failed to resume session"))
            return

        # Re-register as active
        self.router.register(
            channel_id, session.machine_id, session.path, session_id, session.mode
        )

        fallback_msg = " (fresh session with history injected)" if result.get("fallback") else ""
        await self.send_message(
            channel_id,
            f"Session resumed{fallback_msg} on **{session.machine_id}**:`{session.path}`"
        )

    async def cmd_ls(self, channel_id: str, args: list[str]) -> None:
        """/ls machine | /ls session [machine]"""
        if not args:
            await self.send_message(
                channel_id,
                "Usage:\n"
                "`/ls machine` - List all machines\n"
                "`/ls session [machine]` - List sessions"
            )
            return

        subcmd = args[0].lower()

        if subcmd in ("machine", "machines"):
            machines = await self.ssh.list_machines()
            await self.send_message(channel_id, format_machine_list(machines))

        elif subcmd in ("session", "sessions"):
            machine_filter = args[1] if len(args) > 1 else None
            sessions = self.router.list_sessions(machine_filter)
            await self.send_message(channel_id, format_session_list(sessions))

        else:
            await self.send_message(channel_id, "Usage: `/ls machine` or `/ls session [machine]`")

    async def cmd_exit(self, channel_id: str) -> None:
        """/exit - Detach from current session (doesn't destroy it)."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session to exit.")
            return

        self.router.detach(channel_id)
        name_hint = session.name or session.daemon_session_id
        await self.send_message(
            channel_id,
            f"Detached from session on **{session.machine_id}**:`{session.path}`\n"
            f"Use `/resume {name_hint}` to reconnect."
        )

    async def cmd_rm(self, channel_id: str, args: list[str]) -> None:
        """/rm <machine> <path> - Destroy a session."""
        if len(args) < 2:
            await self.send_message(channel_id, "Usage: `/rm <machine_id> <path>`")
            return

        machine_id = args[0]
        path = args[1]

        # Find matching sessions
        sessions = self.router.find_sessions_by_machine_path(machine_id, path)
        if not sessions:
            await self.send_message(channel_id, f"No sessions found for **{machine_id}**:`{path}`")
            return

        for session in sessions:
            if session.status in ("active", "detached"):
                try:
                    local_port = await self.ssh.ensure_tunnel(machine_id)
                    await self.daemon.destroy_session(local_port, session.daemon_session_id)
                except Exception as e:
                    logger.warning(f"Failed to destroy daemon session: {e}")
                self.router.destroy(session.channel_id)

        await self.send_message(
            channel_id,
            f"Destroyed {len(sessions)} session(s) on **{machine_id}**:`{path}`"
        )

    async def cmd_mode(self, channel_id: str, args: list[str]) -> None:
        """/mode <auto|code|plan|ask> - Switch permission mode."""
        if not args:
            await self.send_message(
                channel_id,
                "Usage: `/mode <auto|code|plan|ask>`\n"
                "  **auto (bypass)** - Full auto (skip all permissions)\n"
                "  **code** - Auto accept edits, confirm bash\n"
                "  **plan** - Read-only analysis\n"
                "  **ask** - Confirm everything"
            )
            return

        mode = args[0].lower()
        # Accept both internal and display names
        if mode == "bypass":
            mode = "auto"
        if mode not in ("auto", "code", "plan", "ask"):
            await self.send_message(channel_id, "Invalid mode. Use: `auto` (bypass), `code`, `plan`, or `ask`")
            return

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session. Use `/start` first.")
            return

        local_port = await self.ssh.ensure_tunnel(session.machine_id)
        ok = await self.daemon.set_mode(local_port, session.daemon_session_id, mode)

        if ok:
            self.router.update_mode(channel_id, mode)
            await self.send_message(channel_id, f"Mode set to **{display_mode(mode)}**")
        else:
            await self.send_message(channel_id, format_error("Failed to set mode"))

    async def cmd_status(self, channel_id: str) -> None:
        """/status - Show current session status."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session.")
            return

        queue_stats = None
        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)
            queue_stats = await self.daemon.get_queue_stats(
                local_port, session.daemon_session_id
            )
        except Exception:
            pass

        await self.send_message(channel_id, format_status(session, queue_stats))

    async def cmd_interrupt(self, channel_id: str) -> None:
        """/interrupt - Interrupt Claude's current operation."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session. Use `/start` first.")
            return

        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)
            result = await self.daemon.interrupt_session(
                local_port, session.daemon_session_id
            )

            if result.get("interrupted"):
                await self.send_message(channel_id, "Interrupted Claude's current operation.")
            else:
                await self.send_message(channel_id, "Claude is not currently processing any request.")
        except Exception as e:
            await self.send_message(channel_id, format_error(f"Failed to interrupt: {e}"))

    async def cmd_rename(self, channel_id: str, args: list[str]) -> None:
        """/rename <new_name> - Rename the current session."""
        if not args:
            await self.send_message(channel_id, "Usage: `/rename <new_name>`\nExample: `/rename my-project`")
            return

        new_name = args[0].lower().strip()

        # Validate name format
        from .name_generator import is_valid_name
        if not is_valid_name(new_name):
            await self.send_message(
                channel_id,
                "Invalid name. Use lowercase letters, digits, and hyphens (at least two words).\n"
                "Example: `my-project`, `test-run-1`"
            )
            return

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session. Use `/start` first.")
            return

        old_name = session.name or "(unnamed)"
        if self.router.rename_session(channel_id, new_name):
            await self.send_message(channel_id, f"Session renamed: **{old_name}** -> **{new_name}**")
        else:
            await self.send_message(channel_id, f"Name `{new_name}` is already in use. Choose a different name.")

    async def cmd_health(self, channel_id: str, args: list[str]) -> None:
        """/health [machine] - Check daemon health on a machine."""
        # Determine which machine to check
        machine_id = None
        if args:
            machine_id = args[0]
        else:
            # Try current session's machine
            session = self.router.resolve(channel_id)
            if session:
                machine_id = session.machine_id

        if not machine_id:
            # Check all connected machines
            results: list[str] = []
            for mid in self.config.machines:
                port = self.ssh.get_local_port(mid)
                if port:
                    try:
                        health = await self.daemon.health_check(port)
                        results.append(format_health(mid, health))
                    except Exception as e:
                        results.append(f"**Daemon Health - {mid}**: Error - {e}")
            if not results:
                await self.send_message(channel_id, "No active tunnels. Use `/start` or specify a machine: `/health <machine>`")
                return
            await self.send_message(channel_id, "\n\n".join(results))
            return

        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            health = await self.daemon.health_check(local_port)
            await self.send_message(channel_id, format_health(machine_id, health))
        except Exception as e:
            await self.send_message(channel_id, format_error(f"Health check failed for {machine_id}: {e}"))

    async def cmd_monitor(self, channel_id: str, args: list[str]) -> None:
        """/monitor [machine] - Monitor sessions on a machine."""
        machine_id = None
        if args:
            machine_id = args[0]
        else:
            session = self.router.resolve(channel_id)
            if session:
                machine_id = session.machine_id

        if not machine_id:
            # Monitor all connected machines
            results: list[str] = []
            for mid in self.config.machines:
                port = self.ssh.get_local_port(mid)
                if port:
                    try:
                        monitor = await self.daemon.monitor_sessions(port)
                        results.append(format_monitor(mid, monitor))
                    except Exception as e:
                        results.append(f"**Monitor - {mid}**: Error - {e}")
            if not results:
                await self.send_message(channel_id, "No active tunnels. Use `/start` or specify a machine: `/monitor <machine>`")
                return
            await self.send_message(channel_id, "\n\n".join(results))
            return

        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            monitor = await self.daemon.monitor_sessions(local_port)
            await self.send_message(channel_id, format_monitor(machine_id, monitor))
        except Exception as e:
            await self.send_message(channel_id, format_error(f"Monitor failed for {machine_id}: {e}"))

    async def cmd_help(self, channel_id: str) -> None:
        """/help - Show available commands."""
        help_text = """**Remote Claude Commands:**

`/start <machine> <path>` - Start a new Claude session
`/resume <session_id_or_name>` - Resume a previous session
`/ls machine` - List all machines
`/ls session [machine]` - List sessions
`/exit` - Detach from current session
`/rm <machine> <path>` - Destroy a session
`/mode <auto|code|plan|ask>` - Switch permission mode
`/rename <new_name>` - Rename current session
`/status` - Show current session info
`/health [machine]` - Check daemon health
`/monitor [machine]` - Monitor session details & queues
`/help` - Show this help

After `/start` or `/resume`, send any message to interact with Claude."""
        await self.send_message(channel_id, help_text)

    # ─── Message Forwarding ───

    async def _upload_and_replace_files(
        self,
        machine_id: str,
        text: str,
        file_refs: list | None = None,
    ) -> str:
        """
        Upload file_refs to the remote machine via SCP and replace
        <discord_file>file_id</discord_file> markers with actual remote paths.

        Returns the text with all markers replaced.
        Raises on upload failure (caller should handle).
        """
        if not file_refs:
            return text

        path_mapping = await self.ssh.upload_files(machine_id, file_refs)
        for file_id, remote_path in path_mapping.items():
            text = text.replace(
                f"<discord_file>{file_id}</discord_file>",
                remote_path,
            )
        return text

    async def _forward_message(self, channel_id: str, text: str, file_refs: list | None = None) -> None:
        """Forward a user message to the active Claude session and stream response."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(
                channel_id,
                "No active session. Use `/start <machine> <path>` to begin."
            )
            return

        # Prevent concurrent streaming to the same channel
        if channel_id in self._streaming:
            await self.send_message(channel_id, "Claude is still processing. Please wait...")
            return

        self._streaming.add(channel_id)

        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)

            # Upload files and replace markers before sending to Claude
            if file_refs:
                try:
                    text = await self._upload_and_replace_files(
                        session.machine_id, text, file_refs
                    )
                except Exception as e:
                    await self.send_message(
                        channel_id, format_error(f"File upload failed: {e}")
                    )
                    return

            # Start streaming response
            buffer = ""
            current_msg: Any = None
            last_update = time.time()
            tool_msgs: list[str] = []
            tool_batch: list[dict] = []  # Accumulated tool events for batching
            tool_batch_size = self.config.tool_batch_size

            async for event in self.daemon.send_message(
                local_port, session.daemon_session_id, text
            ):
                event_type = event.get("type", "")

                # Ignore keepalive pings from daemon
                if event_type == "ping":
                    continue

                # Flush tool batch before any non-tool event
                if event_type != "tool_use" and tool_batch:
                    batch_text = compress_tool_messages(tool_batch)
                    tool_msgs.append(batch_text)
                    await self.send_message(channel_id, batch_text)
                    tool_batch = []

                if event_type == "partial":
                    # Streaming text delta
                    content = event.get("content", "")
                    if content:
                        buffer += content
                        now = time.time()

                        # Update message periodically
                        if now - last_update >= STREAM_UPDATE_INTERVAL:
                            if current_msg is None:
                                current_msg = await self.send_message(channel_id, buffer + " ▌")
                            else:
                                # Check if buffer exceeds limit
                                if len(buffer) > STREAM_BUFFER_FLUSH_SIZE:
                                    # Finalize current message, start new one
                                    await self.edit_message(channel_id, current_msg, buffer)
                                    buffer = ""
                                    current_msg = None
                                else:
                                    await self.edit_message(channel_id, current_msg, buffer + " ▌")
                            last_update = now

                elif event_type == "text":
                    # Complete text block
                    content = event.get("content", "")
                    if content:
                        # If we were streaming partials, this replaces them
                        if current_msg:
                            await self.edit_message(channel_id, current_msg, content)
                            current_msg = None
                            buffer = ""
                        else:
                            # Send as new message(s)
                            chunks = split_message(content)
                            for chunk in chunks:
                                await self.send_message(channel_id, chunk)

                elif event_type == "tool_use":
                    # Accumulate tool events; flush when batch is full
                    tool_batch.append(event)
                    if len(tool_batch) >= tool_batch_size:
                        batch_text = compress_tool_messages(tool_batch)
                        tool_msgs.append(batch_text)
                        await self.send_message(channel_id, batch_text)
                        tool_batch = []

                elif event_type == "result":
                    # Claude finished
                    sdk_session_id = event.get("session_id")
                    if sdk_session_id:
                        self.router.update_sdk_session(channel_id, sdk_session_id)

                elif event_type == "system":
                    # System event (init, etc.) - show model info on first connection
                    model = event.get("model")
                    if model and event.get("subtype") == "init":
                        mode_str = display_mode(session.mode)
                        await self.send_message(
                            channel_id,
                            f"Connected to **{model}** | Mode: **{mode_str}**"
                        )

                elif event_type == "queued":
                    position = event.get("position", "?")
                    await self.send_message(
                        channel_id,
                        f"Message queued (position: {position}). Claude is busy with a previous request."
                    )
                    return

                elif event_type == "error":
                    error_msg = event.get("message", "Unknown error")
                    await self.send_message(channel_id, format_error(error_msg))

            # Flush remaining tool batch
            if tool_batch:
                batch_text = compress_tool_messages(tool_batch)
                tool_msgs.append(batch_text)
                await self.send_message(channel_id, batch_text)
                tool_batch = []

            # Flush remaining buffer
            if buffer:
                if current_msg:
                    await self.edit_message(channel_id, current_msg, buffer)
                else:
                    chunks = split_message(buffer)
                    for chunk in chunks:
                        await self.send_message(channel_id, chunk)

        except DaemonConnectionError as e:
            await self.send_message(channel_id, format_error(f"Lost connection to daemon: {e}"))
        except Exception as e:
            logger.exception(f"Error forwarding message to Claude")
            await self.send_message(channel_id, format_error(f"Unexpected error: {e}"))
        finally:
            self._streaming.discard(channel_id)
