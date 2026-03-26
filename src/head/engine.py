"""
BotEngine - Platform-agnostic command engine.

All command logic, streaming, and message forwarding live here.
Uses composition with a PlatformAdapter for platform-specific I/O.
Replaces the old BotBase ABC inheritance pattern.
"""

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .config import (
    Config,
    PeerConfig,
    save_machine_to_config,
    remove_machine_from_config,
    parse_ssh_config,
    format_ssh_hosts_for_display,
    _is_localhost,
)
from .ssh_manager import SSHManager
from .session_router import SessionRouter
from .__version__ import __version__
from .daemon_client import DaemonClient, DaemonError, DaemonConnectionError
from .message_formatter import (
    split_message,
    format_tool_use,
    compress_tool_messages,
    format_activity_message,
    format_tool_line,
    format_machine_list,
    format_session_list,
    format_error,
    format_status,
    format_health,
    format_monitor,
    display_mode,
)
from .file_forward import FileForwardMatcher, ForwardDecision
from .platform.protocol import PlatformAdapter, MessageHandle, FileAttachment

logger = logging.getLogger(__name__)

# How often to update the "streaming" message (seconds)
STREAM_UPDATE_INTERVAL = 1.5

# ── Git URL patterns ──
_GIT_HTTPS_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_GIT_SSH_RE = re.compile(r"^git@[\w.\-]+:([^/]+)/([^/]+?)(?:\.git)?$")
_GIT_GENERIC_HTTPS_RE = re.compile(r"^https?://[^/]+/.+?/([^/]+?)(?:\.git)?/?$")


def _parse_git_url(url: str) -> str | None:
    """Extract repo name from a git URL, or return None if not a git URL."""
    for pattern in (_GIT_HTTPS_RE, _GIT_SSH_RE, _GIT_GENERIC_HTTPS_RE):
        m = pattern.match(url)
        if m:
            return m.group(m.lastindex)
    return None


def resolve_session_path(raw_path: str, project_path: str) -> tuple[str, str | None]:
    """Resolve a user-provided path into an absolute remote path.

    Returns (resolved_path, git_url_or_none).
    - Absolute paths and ~ paths are returned as-is.
    - Git URLs are resolved to {project_path}/{repo_name}.
    - Single words / relative paths are resolved to {project_path}/{raw_path}.
    """
    # Absolute path
    if raw_path.startswith("/"):
        return raw_path, None

    # Home-relative path
    if raw_path.startswith("~"):
        return raw_path, None

    # Git URL
    repo_name = _parse_git_url(raw_path)
    if repo_name:
        return f"{project_path}/{repo_name}", raw_path

    # Single word or relative path → expand under project_path
    return f"{project_path}/{raw_path}", None


# Maximum buffer before forcing a message send
STREAM_BUFFER_FLUSH_SIZE = 1800


class BotEngine:
    """
    Platform-agnostic command engine. All command logic and streaming live here.

    Holds a PlatformAdapter instance and contains all command routing,
    session management, and message forwarding logic.
    """

    def __init__(
        self,
        adapter: PlatformAdapter,
        ssh_manager: SSHManager,
        session_router: SessionRouter,
        daemon_client: DaemonClient,
        config: Config,
        file_pool: Any = None,
    ):
        self.adapter = adapter
        self.ssh = ssh_manager
        self.router = session_router
        self.daemon = daemon_client
        self.config = config
        self.file_pool = file_pool
        # File forwarding matcher (initialized if config enables it)
        self.file_forward: Optional[FileForwardMatcher] = None
        if config.file_forward.enabled:
            self.file_forward = FileForwardMatcher(config.file_forward)
        # Track which channels are currently streaming (to prevent concurrent sends)
        self._streaming: set[str] = set()
        # Track which channels have requested a stop of the current stream
        self._stop_requested: set[str] = set()
        # Track which sessions have already shown the "Connected to" init message
        self._init_shown: set[str] = set()

    # ─── Adapter Wrappers ───

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """Send a message via the adapter."""
        return await self.adapter.send_message(channel_id, text)

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit a message via the adapter."""
        await self.adapter.edit_message(handle, text)

    # ─── Admin Check ───

    async def check_restart_notify(self) -> None:
        """Check for a restart notification file and send a message if found."""
        restart_file = Path.cwd() / ".restart_notify"
        if not restart_file.exists():
            return
        try:
            content = restart_file.read_text().strip().splitlines()
            if len(content) >= 2:
                channel_id = content[0]
                reason = content[1]
                # Only handle if this channel belongs to our platform
                if not channel_id.startswith(f"{self.adapter.platform_name}:"):
                    return
                restart_file.unlink()
                await self.send_message(
                    channel_id,
                    f"**{reason} complete.** Head node is back online. (v{__version__})",
                )
        except Exception as e:
            logger.warning(f"Failed to process restart notify: {e}")

    def is_admin(self, user_id: Optional[int | str]) -> bool:
        """Check if a user ID is in the admin_users list (platform-aware)."""
        if user_id is None:
            return False
        platform = self.adapter.platform_name
        if platform == "discord" and self.config.bot.discord:
            return user_id in (self.config.bot.discord.admin_users or [])
        if platform == "telegram" and self.config.bot.telegram:
            admin_users = getattr(self.config.bot.telegram, "admin_users", None)
            return user_id in (admin_users or []) if admin_users else False
        if platform == "lark" and self.config.bot.lark:
            return str(user_id) in [str(u) for u in (self.config.bot.lark.admin_users or [])]
        return False

    # ─── Command Dispatcher ───

    async def handle_input(
        self,
        channel_id: str,
        text: str,
        user_id: Optional[int] = None,
        attachments: Optional[list[FileAttachment]] = None,
    ) -> None:
        """
        Main entry point: handle a user message from a chat channel.
        Routes to commands or forwards to Claude session.
        """
        text = text.strip()
        if not text:
            return

        # Check for pending interactive flows (SSH import, remove confirmation)
        if not text.startswith("/"):
            if await self._handle_ssh_import_selection(channel_id, text):
                return
            if await self._handle_remove_confirmation(channel_id, text):
                return

        # Check if it's a command
        if text.startswith("/"):
            await self._handle_command(channel_id, text, user_id=user_id)
        else:
            # Forward to active Claude session
            await self._forward_message(channel_id, text)

    async def _handle_command(self, channel_id: str, text: str, user_id: Optional[int] = None) -> None:
        """Parse and dispatch a command."""
        parts = text.split()
        cmd = parts[0].lower()

        # Commands that need all args split individually (variadic)
        variadic_cmds = {"/add-machine", "/addmachine", "/add-peer", "/addpeer"}
        if cmd in variadic_cmds:
            args = parts[1:]
        else:
            # Legacy: split with maxsplit=2 (preserves path args with spaces)
            parts2 = text.split(maxsplit=2)
            args = parts2[1:] if len(parts2) > 1 else []

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
            elif cmd in ("/rm-session", "/rmsession", "/remove-session", "/removesession"):
                await self.cmd_rm_session(channel_id, args)
            elif cmd == "/mode":
                await self.cmd_mode(channel_id, args)
            elif cmd == "/model":
                await self.cmd_model(channel_id, args)
            elif cmd == "/status":
                await self.cmd_status(channel_id)
            elif cmd in ("/interrupt", "/stop"):
                await self.cmd_interrupt(channel_id)
            elif cmd == "/rename":
                await self.cmd_rename(channel_id, args)
            elif cmd == "/health":
                await self.cmd_health(channel_id, args)
            elif cmd == "/monitor":
                await self.cmd_monitor(channel_id, args)
            elif cmd in ("/add-machine", "/addmachine", "/add-peer", "/addpeer"):
                await self.cmd_add_machine(channel_id, args)
            elif cmd in (
                "/remove-machine",
                "/removemachine",
                "/rm-machine",
                "/rmmachine",
                "/remove-peer",
                "/removepeer",
                "/rm-peer",
                "/rmpeer",
            ):
                await self.cmd_remove_machine(channel_id, args)
            elif cmd == "/restart":
                await self.cmd_restart(channel_id, user_id)
            elif cmd == "/update":
                await self.cmd_update(channel_id, user_id)
            elif cmd in ("/tool-display", "/tooldisplay"):
                await self.cmd_tool_display(channel_id, args)
            elif cmd == "/clear":
                await self.cmd_clear(channel_id)
            elif cmd == "/new":
                await self.cmd_new(channel_id)
            elif cmd == "/help":
                await self.cmd_help(channel_id)
            else:
                await self.send_message(
                    channel_id,
                    f"Unknown command: `{cmd}`. Use `/help` for available commands.",
                )
        except DaemonConnectionError as e:
            await self.send_message(channel_id, format_error(f"Cannot connect to daemon: {e}"))
        except DaemonError as e:
            await self.send_message(channel_id, format_error(f"Daemon error: {e}"))
        except Exception as e:
            logger.exception(f"Error handling command: {text}")
            await self.send_message(channel_id, format_error(str(e)))

    # ─── Commands ───

    async def cmd_start(self, channel_id: str, args: list[str], silent_init: bool = False) -> None:
        """/start <machine> <remote_path> [--cli <type>] - Create a new session."""
        if len(args) < 2:
            await self.send_message(
                channel_id,
                "Usage: `/start <peer> <remote_path> [--cli <type>]`\n"
                "Example: `/start gpu-1 ~/project` or `/start gpu-1 myproject --cli codex`\n"
                "Shortcuts: `--codex`, `--gemini`, `--opencode`",
            )
            return

        # Parse --cli <type> or shorthand flags (--codex, --gemini, --opencode)
        cli_type = "claude"
        args = list(args)  # copy to avoid mutating caller's list
        for shorthand in ("--codex", "--gemini", "--opencode"):
            if shorthand in args:
                args.remove(shorthand)
                cli_type = shorthand.lstrip("-")
                break
        if "--cli" in args:
            idx = args.index("--cli")
            if idx + 1 < len(args):
                cli_type = args[idx + 1]
                del args[idx : idx + 2]
            else:
                await self.send_message(
                    channel_id, "Missing value for `--cli`. Usage: `--cli <claude|codex|gemini|opencode>`"
                )
                return

        valid_cli_types = ("claude", "codex", "gemini", "opencode")
        if cli_type not in valid_cli_types:
            await self.send_message(
                channel_id,
                f"Unknown CLI type: **{cli_type}**. Valid types: {', '.join(valid_cli_types)}",
            )
            return

        machine_id = args[0]
        raw_path = args[1]

        # Look up peer config for project_path
        peer = self.config.peers.get(machine_id)
        if not peer:
            await self.send_message(channel_id, f"Unknown peer: **{machine_id}**")
            return

        # Smart path resolution: single word → ~/Projects/word, git URL → clone
        path, git_url = resolve_session_path(raw_path, peer.project_path)
        logger.info(f"Path resolved: {raw_path!r} → {path!r} (project_path={peer.project_path!r})")

        if not silent_init:
            if git_url:
                await self.send_message(
                    channel_id,
                    f"\u26a1 Cloning **{git_url}** on **{machine_id}** → `{path}`...",
                )
            else:
                await self.send_message(channel_id, f"\u26a1 Starting session on **{machine_id}**:`{path}`...")

        # Ensure SSH tunnel
        local_port = await self.ssh.ensure_tunnel(machine_id)

        # Ensure project directory exists and clone if needed
        if git_url:
            try:
                await self.ssh.ensure_repo(machine_id, path, git_url)
            except Exception as exc:
                await self.send_message(channel_id, f"Failed to clone repo: {exc}")
                return
        else:
            # Ensure the directory exists (especially for short-name expansion)
            await self.ssh.ensure_dir(machine_id, path)

        # Sync skills
        await self.ssh.sync_skills(machine_id, path)

        # Create session on daemon (retry once if daemon is unreachable)
        try:
            daemon_session_id = await self.daemon.create_session(
                local_port, path, self.config.default_mode, cli_type=cli_type
            )
        except (DaemonConnectionError, Exception) as e:
            if "Server disconnected" in str(e) or "connect" in str(e).lower():
                logger.info(f"Daemon unreachable on {machine_id}, reconnecting...")
                # Invalidate cached tunnel so ensure_tunnel restarts the daemon
                if machine_id in self.ssh.tunnels:
                    await self.ssh.tunnels[machine_id].close()
                    del self.ssh.tunnels[machine_id]
                local_port = await self.ssh.ensure_tunnel(machine_id)
                daemon_session_id = await self.daemon.create_session(
                    local_port, path, self.config.default_mode, cli_type=cli_type
                )
            else:
                raise

        # Register in session router
        name = self.router.register(
            channel_id,
            machine_id,
            path,
            daemon_session_id,
            self.config.default_mode,
            cli_type=cli_type,
        )

        model_str = getattr(self.config, "default_model", None) or "default"
        cli_display = f" ({cli_type})" if cli_type != "claude" else ""

        await self.send_message(
            channel_id,
            f"\u2705 **Session ready**{cli_display}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"💻 **Peer:** {machine_id}\n"
            f"📂 **Path:** `{path}`\n"
            f"🏷\ufe0f **Name:** {name}\n"
            f"🔐 **Mode:** {display_mode(self.config.default_mode)}\n"
            f"\U0001f9e0 **Model:** {model_str}\n"
            f"🆔 `{daemon_session_id}`\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"\u2328\ufe0f Send a message to start chatting.",
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

        await self.send_message(
            channel_id,
            f"🔄 Resuming session{name_str} on **{session.machine_id}**:`{session.path}`...",
        )

        # Ensure tunnel
        local_port = await self.ssh.ensure_tunnel(session.machine_id)

        # Resume on daemon
        result = await self.daemon.resume_session(local_port, session_id, session.sdk_session_id)

        if not result.get("ok"):
            await self.send_message(channel_id, format_error("Failed to resume session"))
            return

        # Re-register as active
        self.router.register(channel_id, session.machine_id, session.path, session_id, session.mode)

        fallback_msg = " (fresh session with history injected)" if result.get("fallback") else ""
        await self.send_message(
            channel_id,
            f"\u2705 Session resumed{fallback_msg} on **{session.machine_id}**:`{session.path}`",
        )

    async def cmd_ls(self, channel_id: str, args: list[str]) -> None:
        """/ls machine | /ls session [machine]"""
        if not args:
            await self.send_message(
                channel_id,
                "Usage:\n`/ls machine` - List all machines/peers\n`/ls session [machine]` - List sessions",
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
            await self.send_message(channel_id, "\u26a0\ufe0f No active session to exit.")
            return

        self.router.detach(channel_id)
        name_hint = session.name or session.daemon_session_id
        await self.send_message(
            channel_id,
            f"👋 Detached from session on **{session.machine_id}**:`{session.path}`\n"
            f"Use `/resume {name_hint}` to reconnect.",
        )

    async def cmd_rm(self, channel_id: str, args: list[str]) -> None:
        """/rm <machine> <path> - Destroy a session."""
        if len(args) < 2:
            await self.send_message(channel_id, "Usage: `/rm <peer> <path>`")
            return

        machine_id = args[0]
        path = args[1]

        # Find matching sessions
        sessions = self.router.find_sessions_by_machine_path(machine_id, path)
        if not sessions:
            await self.send_message(
                channel_id,
                f"No sessions found for **{machine_id}**:`{path}`",
            )
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
            f"🗑\ufe0f Destroyed {len(sessions)} session(s) on **{machine_id}**:`{path}`",
        )

    async def cmd_rm_session(self, channel_id: str, args: list[str]) -> None:
        """/rm-session <name_or_id> - Destroy a specific session by name or ID."""
        if not args:
            await self.send_message(channel_id, "Usage: `/rm-session <name_or_id>`")
            return

        identifier = args[0]
        session = self.router.find_session_by_name_or_id(identifier)

        if not session:
            await self.send_message(channel_id, f"Session `{identifier}` not found.")
            return

        name_hint = session.name or session.daemon_session_id

        if session.status in ("active", "detached"):
            try:
                local_port = await self.ssh.ensure_tunnel(session.machine_id)
                await self.daemon.destroy_session(local_port, session.daemon_session_id)
            except Exception as e:
                logger.warning(f"Failed to destroy daemon session {name_hint}: {e}")
            self.router.destroy(session.channel_id)

        await self.send_message(
            channel_id,
            f"🗑\ufe0f Destroyed session **{name_hint}** on **{session.machine_id}**:`{session.path}`",
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
                "  **ask** - Confirm everything",
            )
            return

        mode = args[0].lower()
        # Accept both internal and display names
        if mode == "bypass":
            mode = "auto"
        if mode not in ("auto", "code", "plan", "ask"):
            await self.send_message(
                channel_id,
                "Invalid mode. Use: `auto` (bypass), `code`, `plan`, or `ask`",
            )
            return

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session. Use `/start` first.")
            return

        local_port = await self.ssh.ensure_tunnel(session.machine_id)
        ok = await self.daemon.set_mode(local_port, session.daemon_session_id, mode)

        if ok:
            self.router.update_mode(channel_id, mode)
            await self.send_message(channel_id, f"🔐 Mode set to **{display_mode(mode)}**")
        else:
            await self.send_message(channel_id, format_error("Failed to set mode"))

    async def cmd_model(self, channel_id: str, args: list[str]) -> None:
        """/model <model_name> - Switch Claude model."""
        if not args:
            await self.send_message(
                channel_id,
                "Usage: `/model <model_name>`\n"
                "Example: `/model claude-sonnet-4-20250514`\n\n"
                "Common models:\n"
                "  `claude-sonnet-4-20250514` — Claude Sonnet 4\n"
                "  `claude-opus-4-20250115` — Claude Opus 4\n"
                "  `claude-haiku-3-5-20241022` — Claude Haiku 3.5",
            )
            return

        model = args[0]

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session. Use `/start` first.")
            return

        local_port = await self.ssh.ensure_tunnel(session.machine_id)
        ok = await self.daemon.set_model(local_port, session.daemon_session_id, model)

        if ok:
            await self.send_message(channel_id, f"\U0001f9e0 Model set to **{model}**")
        else:
            await self.send_message(channel_id, format_error("Failed to set model"))

    async def cmd_tool_display(self, channel_id: str, args: list[str]) -> None:
        """/tool-display <timer|append|batch> - Switch tool display mode."""
        if not args:
            await self.send_message(
                channel_id,
                "Usage: `/tool-display <timer|append|batch>`\n"
                "  **timer** - Show working timer, send all results at end (default)\n"
                "  **append** - Show each tool call progressively\n"
                "  **batch** - Accumulate tool calls, show summary at end",
            )
            return

        mode = args[0].lower()
        if mode not in ("timer", "append", "batch"):
            await self.send_message(
                channel_id,
                "Invalid tool display mode. Use: `timer`, `append`, or `batch`",
            )
            return

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "⚠️ No active session. Use `/start` first.")
            return

        self.router.update_tool_display(channel_id, mode)
        await self.send_message(channel_id, f"🔧 Tool display set to **{mode}**")

    async def cmd_status(self, channel_id: str) -> None:
        """/status - Show current session status."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session.")
            return

        queue_stats = None
        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)
            queue_stats = await self.daemon.get_queue_stats(local_port, session.daemon_session_id)
        except Exception:
            pass

        await self.send_message(channel_id, format_status(session, queue_stats))

    async def cmd_interrupt(self, channel_id: str) -> None:
        """/interrupt or /stop - Interrupt Claude's current operation."""
        # Signal any active stream to stop
        if channel_id in self._streaming:
            self._stop_requested.add(channel_id)

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session. Use `/start` first.")
            return

        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)
            result = await self.daemon.interrupt_session(local_port, session.daemon_session_id)

            if result.get("interrupted"):
                await self.send_message(channel_id, "\u270b Interrupted Claude's current operation.")
            else:
                await self.send_message(
                    channel_id,
                    "💤 Claude is not currently processing any request.",
                )
        except Exception as e:
            await self.send_message(channel_id, format_error(f"Failed to interrupt: {e}"))

    async def cmd_clear(self, channel_id: str) -> None:
        """/clear - Destroy the current session and start a new one in the same directory."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session. Use `/start` first.")
            return

        machine_id = session.machine_id
        path = session.path
        old_name = session.name or session.daemon_session_id

        await self.send_message(channel_id, f"🧹 Clearing session **{old_name}** and starting fresh...")

        # Destroy old session
        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            await self.daemon.destroy_session(local_port, session.daemon_session_id)
        except Exception as e:
            logger.warning(f"Failed to destroy session during /clear: {e}")
        self.router.destroy(channel_id)

        # Start a new session in the same location
        await self.cmd_start(channel_id, [machine_id, path])

    async def cmd_new(self, channel_id: str) -> None:
        """/new - Destroy the current session and start a fresh one in the same location (like Claude CLI /new)."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(
                channel_id,
                "No active session. Use `/start <machine> <path>` first.",
            )
            return

        machine_id = session.machine_id
        path = session.path
        old_name = session.name or session.daemon_session_id

        await self.send_message(channel_id, f"🔄 Starting fresh session (replacing **{old_name}**)...")

        # Destroy old session
        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            await self.daemon.destroy_session(local_port, session.daemon_session_id)
        except Exception as e:
            logger.warning(f"Failed to destroy session during /new: {e}")
        self.router.destroy(channel_id)

        # Start a new session in the same location
        await self.cmd_start(channel_id, [machine_id, path])

    async def cmd_rename(self, channel_id: str, args: list[str]) -> None:
        """/rename <new_name> - Rename the current session."""
        if not args:
            await self.send_message(
                channel_id,
                "Usage: `/rename <new_name>`\nExample: `/rename my-project`",
            )
            return

        new_name = args[0].lower().strip()

        # Validate name format
        from .name_generator import is_valid_name

        if not is_valid_name(new_name):
            await self.send_message(
                channel_id,
                "Invalid name. Use lowercase letters, digits, and hyphens (at least two words).\n"
                "Example: `my-project`, `test-run-1`",
            )
            return

        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "\u26a0\ufe0f No active session. Use `/start` first.")
            return

        old_name = session.name or "(unnamed)"
        if self.router.rename_session(channel_id, new_name):
            await self.send_message(
                channel_id,
                f"Session renamed: **{old_name}** -> **{new_name}**",
            )
        else:
            await self.send_message(
                channel_id,
                f"Name `{new_name}` is already in use. Choose a different name.",
            )

    async def cmd_health(self, channel_id: str, args: list[str]) -> None:
        """/health [machine] - Check daemon health on a machine."""
        machine_id = None
        if args:
            machine_id = args[0]
        else:
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
                await self.send_message(
                    channel_id,
                    "No active tunnels. Use `/start` or specify a peer: `/health <peer>`",
                )
                return
            await self.send_message(channel_id, "\n\n".join(results))
            return

        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            health = await self.daemon.health_check(local_port)
            await self.send_message(channel_id, format_health(machine_id, health))
        except Exception as e:
            await self.send_message(
                channel_id,
                format_error(f"Health check failed for {machine_id}: {e}"),
            )

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
                await self.send_message(
                    channel_id,
                    "No active tunnels. Use `/start` or specify a peer: `/monitor <peer>`",
                )
                return
            await self.send_message(channel_id, "\n\n".join(results))
            return

        try:
            local_port = await self.ssh.ensure_tunnel(machine_id)
            monitor = await self.daemon.monitor_sessions(local_port)
            await self.send_message(channel_id, format_monitor(machine_id, monitor))
        except Exception as e:
            await self.send_message(
                channel_id,
                format_error(f"Monitor failed for {machine_id}: {e}"),
            )

    async def cmd_add_machine(self, channel_id: str, args: list[str]) -> None:
        """/add-machine <name> [host] [user] [options]"""
        if not args:
            await self.send_message(
                channel_id,
                "Usage:\n"
                "`/add-machine <name>` — Add from SSH config\n"
                "`/add-machine <name> <host> <user> [options]` — Manual\n"
                "`/add-machine --from-ssh` — List all SSH hosts to import\n\n"
                "Options: `--proxy-jump`, `--node-path`, `--password`, "
                "`--port`, `--daemon-port`, `--paths`",
            )
            return

        # --from-ssh: interactive batch import
        if args[0] == "--from-ssh":
            await self._add_machine_from_ssh(channel_id)
            return

        machine_id = args[0]

        # Check for duplicate
        if machine_id in self.config.machines:
            await self.send_message(
                channel_id,
                f"Machine `{machine_id}` already exists. Remove it first with `/remove-machine {machine_id}`.",
            )
            return

        # Parse optional flags
        proxy_jump = None
        node_path = None
        password = None
        port = 22
        daemon_port = 9100
        paths: list[str] = []
        host: str | None = None
        user: str | None = None

        if len(args) >= 3 and not args[1].startswith("--"):
            host = args[1]
            user = args[2]
            flag_start = 3
        else:
            flag_start = 1

        i = flag_start
        while i < len(args):
            flag = args[i]
            if flag == "--proxy-jump" and i + 1 < len(args):
                proxy_jump = args[i + 1]
                i += 2
            elif flag == "--node-path" and i + 1 < len(args):
                node_path = args[i + 1]
                i += 2
            elif flag == "--password" and i + 1 < len(args):
                password = args[i + 1]
                i += 2
            elif flag == "--port" and i + 1 < len(args):
                try:
                    port = int(args[i + 1])
                except ValueError:
                    await self.send_message(channel_id, f"Invalid port: `{args[i + 1]}`")
                    return
                i += 2
            elif flag == "--daemon-port" and i + 1 < len(args):
                try:
                    daemon_port = int(args[i + 1])
                except ValueError:
                    await self.send_message(
                        channel_id,
                        f"Invalid daemon port: `{args[i + 1]}`",
                    )
                    return
                i += 2
            elif flag == "--paths" and i + 1 < len(args):
                paths = [p.strip() for p in args[i + 1].split(",") if p.strip()]
                i += 2
            else:
                await self.send_message(channel_id, f"Unknown option: `{flag}`")
                return

        # If host/user not provided, try SSH config
        if host is None or user is None:
            ssh_entries = parse_ssh_config()
            match = next((e for e in ssh_entries if e.name == machine_id), None)
            if match is None:
                await self.send_message(
                    channel_id,
                    f"Machine `{machine_id}` not found in SSH config.\n"
                    f"Specify host and user: `/add-machine {machine_id} <host> <user>`\n"
                    f"Or use `/add-machine --from-ssh` to browse available hosts.",
                )
                return

            host = host or match.hostname or match.name
            user = user or match.user or os.environ.get("USER", "root")
            if port == 22 and match.port != 22:
                port = match.port
            # Resolve proxy: check proxy_jump first, then extract from ProxyCommand
            effective_proxy = match.proxy_jump
            if not effective_proxy and match.proxy_command:
                # Try to extract the jump host from ProxyCommand
                # Common patterns: "ssh ... jumphost -W %h:%p" or "sshpass ... ssh ... jumphost -W %h:%p"
                import re

                # Match the last word before -W (the jump host in ProxyCommand)
                pc_match = re.search(r"ssh\s+(?:-\S+\s+)*(\S+)\s+-W", match.proxy_command)
                if pc_match:
                    effective_proxy = pc_match.group(1)
                    logger.info(f"Extracted proxy '{effective_proxy}' from ProxyCommand: {match.proxy_command}")

            if proxy_jump is None and effective_proxy:
                if effective_proxy in self.config.machines:
                    proxy_jump = effective_proxy
                else:
                    await self.send_message(
                        channel_id,
                        f"Found `{machine_id}` in SSH config (host=`{host}`, user=`{user}`).\n"
                        f"**Note:** SSH config specifies proxy=`{effective_proxy}` "
                        f"but it's not configured as a machine yet. "
                        f"Add `{effective_proxy}` first, or specify `--proxy-jump` manually.",
                    )
                    return

            await self.send_message(
                channel_id,
                f"Resolved `{machine_id}` from SSH config: "
                f"host=`{host}`, user=`{user}`" + (f", proxy=`{proxy_jump}`" if proxy_jump else ""),
            )

        # Validate proxy_jump references an existing machine
        if proxy_jump and proxy_jump not in self.config.machines:
            await self.send_message(
                channel_id,
                f"Proxy jump host `{proxy_jump}` not found. Available machines: "
                f"{', '.join(self.config.machines.keys())}",
            )
            return

        # Detect localhost
        is_local = _is_localhost(host)

        mc = PeerConfig(
            id=machine_id,
            transport="local" if is_local else "ssh",
            ssh_host=host,
            ssh_user=user,
            ssh_port=port,
            proxy_jump=proxy_jump,
            password=password,
            daemon_port=daemon_port,
            node_path=node_path,
            default_paths=paths,
        )

        # Add to runtime config
        self.config.machines[machine_id] = mc

        # Persist to config.yaml
        try:
            save_machine_to_config(self.config, mc)
        except Exception as e:
            logger.warning(f"Failed to save to config.yaml: {e}")
            await self.send_message(
                channel_id,
                f"**Warning:** Machine added to runtime but failed to save to config.yaml: {e}",
            )

        local_tag = " (localhost)" if is_local else ""
        proxy_tag = f" via `{proxy_jump}`" if proxy_jump else ""
        await self.send_message(
            channel_id,
            f"Machine **{machine_id}** added{local_tag}{proxy_tag}\n"
            f"Host: `{host}` | User: `{user}` | Port: {port}\n"
            f"Daemon port: {daemon_port}"
            + (f"\nPaths: {', '.join(f'`{p}`' for p in paths)}" if paths else "")
            + (f"\nNode: `{node_path}`" if node_path else ""),
        )

    async def _add_machine_from_ssh(self, channel_id: str) -> None:
        """Parse SSH config and let user select hosts to import."""
        await self.send_message(channel_id, "Parsing SSH config...")

        entries = parse_ssh_config()
        if not entries:
            await self.send_message(channel_id, "No SSH hosts found in `~/.ssh/config`.")
            return

        # Filter out hosts that are already configured
        existing = set(self.config.machines.keys())
        new_entries = [e for e in entries if e.name not in existing]

        if not new_entries:
            await self.send_message(
                channel_id,
                "All SSH hosts are already configured as machines.",
            )
            return

        # Store the entries for later selection
        self._ssh_import_entries = new_entries
        self._ssh_import_channel = channel_id

        display = format_ssh_hosts_for_display(new_entries)
        await self.send_message(channel_id, display)

    async def _handle_ssh_import_selection(self, channel_id: str, text: str) -> bool:
        """Handle user's response to SSH import listing."""
        if not hasattr(self, "_ssh_import_entries") or not hasattr(self, "_ssh_import_channel"):
            return False
        if channel_id != self._ssh_import_channel:
            return False

        entries = self._ssh_import_entries

        # Parse selection (numbers separated by spaces)
        try:
            indices = [int(x.strip()) for x in text.strip().split() if x.strip().isdigit()]
        except ValueError:
            return False

        if not indices:
            del self._ssh_import_entries
            del self._ssh_import_channel
            return False

        added: list[str] = []
        errors: list[str] = []

        for idx in indices:
            if idx < 1 or idx > len(entries):
                errors.append(f"Index {idx} out of range")
                continue

            entry = entries[idx - 1]

            if entry.name in self.config.machines:
                errors.append(f"`{entry.name}` already exists")
                continue

            proxy_jump = None
            # Check proxy_jump first, then try to extract from ProxyCommand
            effective_proxy = entry.proxy_jump
            if not effective_proxy and entry.proxy_command:
                import re

                pc_match = re.search(r"ssh\s+(?:-\S+\s+)*(\S+)\s+-W", entry.proxy_command)
                if pc_match:
                    effective_proxy = pc_match.group(1)
            if effective_proxy and effective_proxy in self.config.machines:
                proxy_jump = effective_proxy

            host = entry.hostname or entry.name
            is_local = _is_localhost(host)

            mc = PeerConfig(
                id=entry.name,
                transport="local" if is_local else "ssh",
                ssh_host=host,
                ssh_user=entry.user or os.environ.get("USER", "root"),
                ssh_port=entry.port,
                proxy_jump=proxy_jump,
                password=None,
                daemon_port=9100,
                node_path=None,
                default_paths=[],
            )

            self.config.machines[entry.name] = mc
            try:
                save_machine_to_config(self.config, mc)
            except Exception as e:
                logger.warning(f"Failed to save {entry.name} to config: {e}")

            local_tag = " (localhost)" if is_local else ""
            added.append(f"**{entry.name}**{local_tag}")

        # Clean up state
        del self._ssh_import_entries
        del self._ssh_import_channel

        result_parts: list[str] = []
        if added:
            result_parts.append(f"Added {len(added)} machine(s): {', '.join(added)}")
        if errors:
            result_parts.append(f"Errors: {'; '.join(errors)}")

        await self.send_message(channel_id, "\n".join(result_parts) or "No machines added.")
        return True

    async def cmd_remove_machine(self, channel_id: str, args: list[str]) -> None:
        """/remove-machine <machine_id> - Remove a machine from config."""
        if not args:
            await self.send_message(channel_id, "Usage: `/remove-machine <machine_id>`")
            return

        machine_id = args[0]

        if machine_id not in self.config.machines:
            await self.send_message(channel_id, f"Machine `{machine_id}` not found.")
            return

        # Check if this machine is a proxy_jump target for other machines
        dependents = [
            mid for mid, mc in self.config.machines.items() if mc.proxy_jump == machine_id and mid != machine_id
        ]
        if dependents:
            await self.send_message(
                channel_id,
                f"Cannot remove **{machine_id}**: it is used as `proxy_jump` by: "
                f"{', '.join(f'`{d}`' for d in dependents)}\n"
                f"Remove those machines first.",
            )
            return

        # Check for active sessions
        sessions = self.router.list_sessions(machine_id)
        active_sessions = [s for s in sessions if s.status in ("active", "detached")]

        if active_sessions:
            self._remove_confirm_machine = machine_id
            self._remove_confirm_channel = channel_id
            self._remove_confirm_sessions = active_sessions

            session_list = "\n".join(f"  - `{s.daemon_session_id}` ({s.status}) at `{s.path}`" for s in active_sessions)
            await self.send_message(
                channel_id,
                f"Machine **{machine_id}** has {len(active_sessions)} active session(s):\n"
                f"{session_list}\n\n"
                f"These sessions will be detached. Type `yes` to confirm or `no` to cancel.",
            )
            return

        # No active sessions, remove directly
        await self._do_remove_machine(channel_id, machine_id)

    async def _handle_remove_confirmation(self, channel_id: str, text: str) -> bool:
        """Handle user's confirmation for machine removal."""
        if not hasattr(self, "_remove_confirm_machine") or not hasattr(self, "_remove_confirm_channel"):
            return False
        if channel_id != self._remove_confirm_channel:
            return False

        machine_id = self._remove_confirm_machine
        answer = text.strip().lower()

        # Clean up state
        del self._remove_confirm_machine
        del self._remove_confirm_channel
        sessions = self._remove_confirm_sessions
        del self._remove_confirm_sessions

        if answer in ("yes", "y"):
            for s in sessions:
                try:
                    if s.status == "active":
                        self.router.detach(s.channel_id)
                except Exception as e:
                    logger.warning(f"Failed to detach session {s.daemon_session_id}: {e}")

            await self._do_remove_machine(channel_id, machine_id)
        else:
            await self.send_message(
                channel_id,
                f"Removal of **{machine_id}** cancelled.",
            )

        return True

    async def _do_remove_machine(self, channel_id: str, machine_id: str) -> None:
        """Actually remove a machine from config and runtime."""
        if machine_id in self.ssh.tunnels:
            tunnel = self.ssh.tunnels[machine_id]
            await tunnel.close()
            del self.ssh.tunnels[machine_id]

        del self.config.machines[machine_id]

        try:
            remove_machine_from_config(self.config, machine_id)
        except Exception as e:
            logger.warning(f"Failed to remove from config.yaml: {e}")
            await self.send_message(
                channel_id,
                f"**Warning:** Removed from runtime but failed to update config.yaml: {e}",
            )

        await self.send_message(channel_id, f"Machine **{machine_id}** removed.")

    async def cmd_restart(self, channel_id: str, user_id: Optional[int] = None) -> None:
        """/restart - Restart the head node process (admin only)."""
        if not self.is_admin(user_id):
            await self.send_message(
                channel_id,
                "**Error:** `/restart` requires admin privileges.",
            )
            return

        await self.send_message(channel_id, f"Restarting head node... (v{__version__})")
        logger.info(f"Restart requested by user {user_id}")

        await asyncio.sleep(1)
        self._do_restart(channel_id, "Restart")

    async def cmd_update(self, channel_id: str, user_id: Optional[int] = None) -> None:
        """/update - Git pull and restart (admin only)."""
        if not self.is_admin(user_id):
            await self.send_message(
                channel_id,
                "**Error:** `/update` requires admin privileges.",
            )
            return

        project_dir = str(Path(__file__).resolve().parent.parent)
        await self.send_message(channel_id, f"Pulling latest code... (v{__version__})")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "pull", "--ff-only"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            await self.send_message(
                channel_id,
                format_error("Git pull timed out after 30s."),
            )
            return
        except FileNotFoundError:
            await self.send_message(
                channel_id,
                format_error("Git not found on this machine."),
            )
            return

        if result.returncode != 0:
            stderr = result.stderr.strip()[:500] if result.stderr else "(no output)"
            await self.send_message(
                channel_id,
                format_error(f"Git pull failed:\n```\n{stderr}\n```"),
            )
            return

        stdout = result.stdout.strip() if result.stdout else "(no output)"
        if "Already up to date" in stdout:
            await self.send_message(
                channel_id,
                f"Already up to date (v{__version__}). No restart needed.\n```\n{stdout}\n```",
            )
            return

        await self.send_message(
            channel_id,
            f"Updated:\n```\n{stdout}\n```\nRestarting...",
        )
        logger.info(f"Update requested by user {user_id}: {stdout}")

        await asyncio.sleep(1)
        self._do_restart(channel_id, "Update")

    @staticmethod
    def _do_restart(channel_id: str | None = None, reason: str = "Restart") -> None:
        """Replace this process with a fresh copy via os.execv."""
        from . import main as main_module

        exe = main_module._startup_executable
        config_path = main_module._startup_config_path
        workdir = main_module._startup_workdir

        if channel_id:
            restart_file = Path(workdir) / ".restart_notify"
            try:
                restart_file.write_text(f"{channel_id}\n{reason}\n")
            except Exception as e:
                logger.warning(f"Failed to write restart notify file: {e}")

        args = [exe, "-m", "head.main", config_path]
        logger.info(f"Restarting: {' '.join(args)} (cwd={workdir})")

        os.chdir(workdir)
        os.execv(exe, args)

    async def cmd_help(self, channel_id: str) -> None:
        """/help - Show available commands."""
        help_text = """**Codecast Commands:**

`/start <peer> <remote_path>` - Start a new Claude session
`/resume <session_id_or_name>` - Resume a previous session
`/new` - Start a new session in the same directory
`/clear` - Clear context: destroy + restart in same directory
`/ls machine` - List all machines/peers
`/ls session [machine]` - List sessions
`/exit` - Detach from current session
`/rm <peer> <path>` - Destroy session(s) by machine and path
`/rm-session <name_or_id>` - Destroy a specific session by name or ID
`/mode <auto|code|plan|ask>` - Switch permission mode
`/model <model_name>` - Switch Claude model
`/tool-display <append|batch>` - Switch tool display mode
`/stop` - Stop Claude's current response
`/interrupt` - Interrupt Claude's current operation
`/rename <new_name>` - Rename current session
`/status` - Show current session info
`/health [peer]` - Check daemon health
`/monitor [peer]` - Monitor session details & queues
`/add-machine <name>` - Add machine (from SSH config)
`/add-machine --from-ssh` - Browse all SSH hosts
`/add-peer <name>` - Add peer (alias for /add-machine)
`/remove-machine <machine>` - Remove a machine
`/remove-peer <peer>` - Remove peer (alias for /remove-machine)
`/update` - Pull latest code and restart (admin)
`/restart` - Restart head node (admin)
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
        <file_ref>file_id</file_ref> markers with actual remote paths.

        For text files (.txt, .md, .markdown), reads content and appends
        it inline instead of uploading.
        """
        if not file_refs:
            return text

        # Separate text files (content to inline) from other files (to upload)
        text_files = []
        upload_files = []
        for ref in file_refs:
            ext = Path(ref.original_name).suffix.lower()
            if ext in (".txt", ".md", ".markdown"):
                text_files.append(ref)
            else:
                upload_files.append(ref)

        # For text files, read content and append to message
        for ref in text_files:
            try:
                content = Path(ref.local_path).read_text(encoding="utf-8", errors="replace")
                # Remove the file marker from text
                text = text.replace(f"<file_ref>{ref.file_id}</file_ref>", "")
                text = text.replace(f"<discord_file>{ref.file_id}</discord_file>", "")
                # Append file content
                text += f"\n\n--- {ref.original_name} ---\n{content}"
            except Exception as e:
                logger.warning(f"Failed to read text file {ref.original_name}: {e}")

        # Upload remaining files normally
        if upload_files:
            path_mapping = await self.ssh.upload_files(machine_id, upload_files)
            for file_id, remote_path in path_mapping.items():
                text = text.replace(f"<file_ref>{file_id}</file_ref>", remote_path)
                text = text.replace(f"<discord_file>{file_id}</discord_file>", remote_path)

        return text

    async def _detect_and_forward_files(self, channel_id: str, machine_id: str, text: str) -> None:
        """Detect file paths in text and forward matching files."""
        if not self.file_forward:
            return

        paths = self.file_forward.detect_paths(text, channel_id)
        for path in paths:
            # Pre-download intent check (file_size=0 skips size validation)
            decision = self.file_forward.should_forward(path, file_size=0)

            if decision.action == "auto_send":
                local_path = None
                try:
                    local_path = await self.ssh.download_file(machine_id, path, self.file_forward.config.download_dir)
                    actual_size = local_path.stat().st_size
                    # Authoritative size check with actual file
                    decision = self.file_forward.should_forward(path, actual_size)
                    if decision.action == "auto_send":
                        filename = Path(path).name
                        await self.adapter.send_file(channel_id, local_path, caption=f"{filename}")
                    else:
                        await self.send_message(
                            channel_id,
                            f"File `{Path(path).name}` ({actual_size // 1024}KB) exceeds size limit. {decision.reason}",
                        )
                except Exception as e:
                    logger.warning(f"Failed to forward file {path}: {e}")
                finally:
                    if local_path and local_path.exists():
                        local_path.unlink(missing_ok=True)

            elif decision.action == "notify":
                await self.send_message(
                    channel_id,
                    f"Detected file: `{path}` — {decision.reason}",
                )

    async def _forward_message(
        self,
        channel_id: str,
        text: str,
        file_refs: list | None = None,
    ) -> None:
        """
        Forward a user message to the active Claude session and stream response.

        Uses a 2-message model:
        - Message 1 (activity): Accumulates tool calls + thinking, edited in-place
        - Message 2+ (result): Final text output, sent as new message(s)
        """
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(
                channel_id,
                "No active session. Use `/start <machine> <path>` to begin.",
            )
            return

        # Prevent concurrent streaming to the same channel
        if channel_id in self._streaming:
            await self.send_message(channel_id, "Claude is still processing. Please wait...")
            return

        self._streaming.add(channel_id)

        # Start typing indicator
        await self.adapter.start_typing(channel_id)

        # Reset file forward dedup for this stream
        if self.file_forward:
            self.file_forward.reset(channel_id)

        try:
            local_port = await self.ssh.ensure_tunnel(session.machine_id)

            # Upload files and replace markers before sending to Claude
            if file_refs:
                try:
                    text = await self._upload_and_replace_files(session.machine_id, text, file_refs)
                except Exception as e:
                    await self.send_message(channel_id, format_error(f"File upload failed: {e}"))
                    return

            tool_display = session.tool_display  # "timer", "append", or "batch"

            # ── Timer mode: show "Working Xs" with periodic edits, send all text at end ──
            if tool_display == "timer":
                timer_msg: Optional[MessageHandle] = None
                timer_start = time.time()
                result_texts: list[str] = []
                timer_done = False

                def _format_elapsed() -> str:
                    elapsed = int(time.time() - timer_start)
                    mins, secs = divmod(elapsed, 60)
                    return f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"

                async def _timer_loop() -> None:
                    """Edit the timer message every 30s."""
                    nonlocal timer_msg
                    while not timer_done:
                        await asyncio.sleep(30)
                        if timer_done or timer_msg is None:
                            break
                        try:
                            await self.edit_message(timer_msg, f"Working {_format_elapsed()}")
                        except Exception:
                            pass

                timer_task: Optional[asyncio.Task] = None

                async for event in self.daemon.send_message(local_port, session.daemon_session_id, text):
                    if channel_id in self._stop_requested:
                        break

                    event_type = event.get("type", "")

                    if event_type == "ping":
                        continue

                    # tool_use / partial — silently consumed, just ensure timer is running
                    if event_type in ("tool_use", "partial"):
                        if timer_msg is None:
                            timer_msg = await self.send_message(channel_id, f"Working 0s")
                            timer_task = asyncio.create_task(_timer_loop())
                        continue

                    if event_type == "text":
                        content = event.get("content", "")
                        if content:
                            result_texts.append(content)
                        continue

                    if event_type == "result":
                        sdk_session_id = event.get("session_id")
                        if sdk_session_id:
                            self.router.update_sdk_session(channel_id, sdk_session_id)

                    elif event_type == "system":
                        model = event.get("model")
                        if model and event.get("subtype") == "init":
                            session = self.router.resolve(channel_id)
                            daemon_sid = session.daemon_session_id if session else ""
                            if daemon_sid not in self._init_shown:
                                self._init_shown.add(daemon_sid)
                                mode_str = display_mode(session.mode) if session else "unknown"
                                name_str = f" | Session: **{session.name}**" if session and session.name else ""
                                await self.send_message(
                                    channel_id,
                                    f"Connected to **{model}** | Mode: **{mode_str}**{name_str}",
                                )

                    elif event_type == "queued":
                        position = event.get("position", "?")
                        await self.send_message(
                            channel_id,
                            f"Message queued (position: {position}). Claude is busy with a previous request.",
                        )
                        return

                    elif event_type == "error":
                        error_msg = event.get("message", "Unknown error")
                        await self.send_message(channel_id, format_error(error_msg))

                # Stream finished — stop timer, send results
                timer_done = True
                if timer_task is not None:
                    timer_task.cancel()
                    try:
                        await timer_task
                    except asyncio.CancelledError:
                        pass

                if timer_msg is not None:
                    try:
                        await self.edit_message(timer_msg, f"Done in {_format_elapsed()}")
                    except Exception:
                        pass

                if result_texts:
                    full_text = "\n\n".join(result_texts)
                    for chunk in split_message(full_text):
                        await self.send_message(channel_id, chunk)
                    if self.file_forward:
                        await self._detect_and_forward_files(channel_id, session.machine_id, full_text)

            # ── Append / Batch mode: progressive tool display ──
            else:
                activity_msg: Optional[MessageHandle] = None
                activity_lines: list[str] = []
                batch_lines: list[str] = []
                thinking_buf: str = ""
                last_activity_update = time.time()

                async def update_activity():
                    """Edit or create the activity message with current tool lines + thinking."""
                    nonlocal activity_msg, activity_lines, last_activity_update
                    content = format_activity_message(activity_lines, thinking_buf, cursor=True)
                    if not content.strip():
                        return
                    if len(content) > 1900 and activity_msg is not None:
                        await finalize_activity()
                        content = format_activity_message(activity_lines, thinking_buf, cursor=True)
                    if activity_msg is None:
                        activity_msg = await self.send_message(channel_id, content)
                    else:
                        await self.edit_message(activity_msg, content)
                    last_activity_update = time.time()

                async def finalize_activity():
                    """Remove cursor/thinking from activity message, freeze it."""
                    nonlocal activity_msg, activity_lines
                    if activity_msg is not None:
                        if activity_lines:
                            final = format_activity_message(activity_lines, "", cursor=False)
                            await self.edit_message(activity_msg, final)
                        else:
                            try:
                                await self.adapter.delete_message(activity_msg)
                            except Exception:
                                pass
                    activity_msg = None
                    activity_lines = []

                async def flush_batch():
                    """Send accumulated batch tool lines as a single summary message."""
                    nonlocal batch_lines
                    if batch_lines:
                        summary = format_activity_message(batch_lines, "", cursor=False)
                        if summary.strip():
                            await self.send_message(channel_id, summary)
                        batch_lines = []

                async for event in self.daemon.send_message(local_port, session.daemon_session_id, text):
                    if channel_id in self._stop_requested:
                        break

                    event_type = event.get("type", "")

                    if event_type == "ping":
                        continue

                    if event_type == "tool_use":
                        line = format_tool_line(event)
                        thinking_buf = ""
                        if tool_display == "batch":
                            batch_lines.append(line)
                        else:
                            activity_lines.append(line)
                            await update_activity()

                    elif event_type == "partial":
                        content = event.get("content", "")
                        if content:
                            thinking_buf += content
                            now = time.time()
                            if now - last_activity_update >= STREAM_UPDATE_INTERVAL:
                                await update_activity()

                    elif event_type == "text":
                        content = event.get("content", "")
                        if content:
                            thinking_buf = ""
                            await finalize_activity()
                            chunks = split_message(content)
                            for chunk in chunks:
                                await self.send_message(channel_id, chunk)
                            if self.file_forward:
                                await self._detect_and_forward_files(channel_id, session.machine_id, content)

                    elif event_type == "result":
                        sdk_session_id = event.get("session_id")
                        if sdk_session_id:
                            self.router.update_sdk_session(channel_id, sdk_session_id)

                    elif event_type == "system":
                        model = event.get("model")
                        if model and event.get("subtype") == "init":
                            session = self.router.resolve(channel_id)
                            daemon_sid = session.daemon_session_id if session else ""
                            if daemon_sid not in self._init_shown:
                                self._init_shown.add(daemon_sid)
                                mode_str = display_mode(session.mode) if session else "unknown"
                                name_str = f" | Session: **{session.name}**" if session and session.name else ""
                                await self.send_message(
                                    channel_id,
                                    f"Connected to **{model}** | Mode: **{mode_str}**{name_str}",
                                )

                    elif event_type == "queued":
                        position = event.get("position", "?")
                        await self.send_message(
                            channel_id,
                            f"Message queued (position: {position}). Claude is busy with a previous request.",
                        )
                        return

                    elif event_type == "error":
                        error_msg = event.get("message", "Unknown error")
                        await self.send_message(channel_id, format_error(error_msg))

                thinking_buf = ""
                await finalize_activity()
                await flush_batch()

        except DaemonConnectionError as e:
            await self.send_message(
                channel_id,
                format_error(f"Lost connection to daemon: {e}"),
            )
        except Exception as e:
            logger.exception("Error forwarding message to Claude")
            await self.send_message(channel_id, format_error(f"Unexpected error: {e}"))
        finally:
            await self.adapter.stop_typing(channel_id)
            self._streaming.discard(channel_id)
            self._stop_requested.discard(channel_id)
            if self.file_forward:
                self.file_forward.cleanup(channel_id)
