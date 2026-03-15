"""
Bot Base - abstract base class for Discord and Telegram bots.

Contains all the shared command handling and message forwarding logic.
Subclasses implement platform-specific send/edit operations.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from .config import Config, MachineConfig, save_machine_to_config, remove_machine_from_config, parse_ssh_config, format_ssh_hosts_for_display, _is_localhost
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

    # ─── Admin Check ───

    def is_admin(self, user_id: Optional[int]) -> bool:
        """Check if a user ID is in the admin_users list."""
        if user_id is None:
            return False
        dc = self.config.bot.discord
        if dc and dc.admin_users:
            return user_id in dc.admin_users
        # If no admin_users configured, nobody is admin
        return False

    # ─── Command Dispatcher ───

    async def handle_input(self, channel_id: str, text: str, user_id: Optional[int] = None) -> None:
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
        variadic_cmds = {"/add-machine", "/addmachine"}
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
            elif cmd in ("/add-machine", "/addmachine"):
                await self.cmd_add_machine(channel_id, args)
            elif cmd in ("/remove-machine", "/removemachine", "/rm-machine", "/rmmachine"):
                await self.cmd_remove_machine(channel_id, args)
            elif cmd == "/restart":
                await self.cmd_restart(channel_id, user_id)
            elif cmd == "/update":
                await self.cmd_update(channel_id, user_id)
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

    async def cmd_add_machine(self, channel_id: str, args: list[str]) -> None:
        """
        /add-machine <name> [host] [user] [options]

        If only <name> is given, tries to resolve from ~/.ssh/config.
        If <name> <host> <user> are given, uses those directly.
        """
        if not args:
            await self.send_message(
                channel_id,
                "Usage:\n"
                "`/add-machine <name>` — Add from SSH config\n"
                "`/add-machine <name> <host> <user> [options]` — Manual\n"
                "`/add-machine --from-ssh` — List all SSH hosts to import\n\n"
                "Options: `--proxy-jump`, `--node-path`, `--password`, "
                "`--port`, `--daemon-port`, `--paths`"
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
                f"Machine `{machine_id}` already exists. "
                f"Remove it first with `/remove-machine {machine_id}`."
            )
            return

        # Parse optional flags from remaining args (skip first 1 or 3 positional args)
        proxy_jump = None
        node_path = None
        password = None
        port = 22
        daemon_port = 9100
        paths: list[str] = []
        host: str | None = None
        user: str | None = None

        if len(args) >= 3 and not args[1].startswith("--"):
            # /add-machine <id> <host> <user> [opts...]
            host = args[1]
            user = args[2]
            flag_start = 3
        else:
            # /add-machine <id> [opts...] — resolve from SSH config
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
                    await self.send_message(channel_id, f"Invalid daemon port: `{args[i + 1]}`")
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
                    f"Or use `/add-machine --from-ssh` to browse available hosts."
                )
                return

            host = host or match.hostname or match.name
            user = user or match.user or os.environ.get("USER", "root")
            if port == 22 and match.port != 22:
                port = match.port
            if proxy_jump is None and match.proxy_jump:
                # Only use SSH config proxy_jump if the jump host is already
                # configured as a machine (otherwise it won't work)
                if match.proxy_jump in self.config.machines:
                    proxy_jump = match.proxy_jump
                else:
                    await self.send_message(
                        channel_id,
                        f"Found `{machine_id}` in SSH config (host=`{host}`, user=`{user}`).\n"
                        f"**Note:** SSH config specifies proxy_jump=`{match.proxy_jump}` "
                        f"but it's not configured as a machine yet. "
                        f"Add `{match.proxy_jump}` first, or specify `--proxy-jump` manually."
                    )
                    return

            await self.send_message(
                channel_id,
                f"Resolved `{machine_id}` from SSH config: "
                f"host=`{host}`, user=`{user}`"
                + (f", proxy=`{proxy_jump}`" if proxy_jump else "")
            )

        # Validate proxy_jump references an existing machine
        if proxy_jump and proxy_jump not in self.config.machines:
            await self.send_message(
                channel_id,
                f"Proxy jump host `{proxy_jump}` not found. Available machines: "
                f"{', '.join(self.config.machines.keys())}"
            )
            return

        # Detect localhost
        is_local = _is_localhost(host)

        mc = MachineConfig(
            id=machine_id,
            host=host,
            user=user,
            port=port,
            proxy_jump=proxy_jump,
            password=password,
            daemon_port=daemon_port,
            node_path=node_path,
            default_paths=paths,
            localhost=is_local,
        )

        # Add to runtime config
        self.config.machines[machine_id] = mc

        # Persist to config.yaml
        try:
            save_machine_to_config(self.config, mc)
        except Exception as e:
            logger.warning(f"Failed to save to config.yaml: {e}")
            await self.send_message(channel_id, f"**Warning:** Machine added to runtime but failed to save to config.yaml: {e}")

        local_tag = " (localhost)" if is_local else ""
        proxy_tag = f" via `{proxy_jump}`" if proxy_jump else ""
        await self.send_message(
            channel_id,
            f"Machine **{machine_id}** added{local_tag}{proxy_tag}\n"
            f"Host: `{host}` | User: `{user}` | Port: {port}\n"
            f"Daemon port: {daemon_port}"
            + (f"\nPaths: {', '.join(f'`{p}`' for p in paths)}" if paths else "")
            + (f"\nNode: `{node_path}`" if node_path else "")
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
            await self.send_message(channel_id, "All SSH hosts are already configured as machines.")
            return

        # Store the entries for later selection
        self._ssh_import_entries = new_entries  # type: ignore[attr-defined]
        self._ssh_import_channel = channel_id  # type: ignore[attr-defined]

        display = format_ssh_hosts_for_display(new_entries)
        await self.send_message(channel_id, display)

    async def _handle_ssh_import_selection(self, channel_id: str, text: str) -> bool:
        """
        Handle user's response to SSH import listing.
        Returns True if the input was consumed as an import selection.
        """
        if not hasattr(self, '_ssh_import_entries') or not hasattr(self, '_ssh_import_channel'):
            return False
        if channel_id != self._ssh_import_channel:  # type: ignore[attr-defined]
            return False

        entries = self._ssh_import_entries  # type: ignore[attr-defined]

        # Parse selection (numbers separated by spaces)
        try:
            indices = [int(x.strip()) for x in text.strip().split() if x.strip().isdigit()]
        except ValueError:
            return False

        if not indices:
            # Not a valid selection, clean up and pass through
            del self._ssh_import_entries  # type: ignore[attr-defined]
            del self._ssh_import_channel  # type: ignore[attr-defined]
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

            # Resolve proxy_jump: if the SSH entry has a proxy_jump that matches
            # an existing machine, use it; otherwise skip proxy_jump
            proxy_jump = None
            if entry.proxy_jump and entry.proxy_jump in self.config.machines:
                proxy_jump = entry.proxy_jump

            host = entry.hostname or entry.name
            is_local = _is_localhost(host)

            mc = MachineConfig(
                id=entry.name,
                host=host,
                user=entry.user or os.environ.get("USER", "root"),
                port=entry.port,
                proxy_jump=proxy_jump,
                password=None,
                daemon_port=9100,
                node_path=None,
                default_paths=[],
                localhost=is_local,
            )

            self.config.machines[entry.name] = mc
            try:
                save_machine_to_config(self.config, mc)
            except Exception as e:
                logger.warning(f"Failed to save {entry.name} to config: {e}")

            local_tag = " (localhost)" if is_local else ""
            added.append(f"**{entry.name}**{local_tag}")

        # Clean up state
        del self._ssh_import_entries  # type: ignore[attr-defined]
        del self._ssh_import_channel  # type: ignore[attr-defined]

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
            mid for mid, mc in self.config.machines.items()
            if mc.proxy_jump == machine_id and mid != machine_id
        ]
        if dependents:
            await self.send_message(
                channel_id,
                f"Cannot remove **{machine_id}**: it is used as `proxy_jump` by: "
                f"{', '.join(f'`{d}`' for d in dependents)}\n"
                f"Remove those machines first."
            )
            return

        # Check for active sessions
        sessions = self.router.list_sessions(machine_id)
        active_sessions = [s for s in sessions if s.status in ("active", "detached")]

        if active_sessions:
            # Store pending confirmation
            self._remove_confirm_machine = machine_id  # type: ignore[attr-defined]
            self._remove_confirm_channel = channel_id  # type: ignore[attr-defined]
            self._remove_confirm_sessions = active_sessions  # type: ignore[attr-defined]

            session_list = "\n".join(
                f"  - `{s.daemon_session_id}` ({s.status}) at `{s.path}`"
                for s in active_sessions
            )
            await self.send_message(
                channel_id,
                f"Machine **{machine_id}** has {len(active_sessions)} active session(s):\n"
                f"{session_list}\n\n"
                f"These sessions will be detached. Type `yes` to confirm or `no` to cancel."
            )
            return

        # No active sessions, remove directly
        await self._do_remove_machine(channel_id, machine_id)

    async def _handle_remove_confirmation(self, channel_id: str, text: str) -> bool:
        """
        Handle user's confirmation for machine removal.
        Returns True if the input was consumed.
        """
        if not hasattr(self, '_remove_confirm_machine') or not hasattr(self, '_remove_confirm_channel'):
            return False
        if channel_id != self._remove_confirm_channel:  # type: ignore[attr-defined]
            return False

        machine_id = self._remove_confirm_machine  # type: ignore[attr-defined]
        answer = text.strip().lower()

        # Clean up state
        del self._remove_confirm_machine  # type: ignore[attr-defined]
        del self._remove_confirm_channel  # type: ignore[attr-defined]
        sessions = self._remove_confirm_sessions  # type: ignore[attr-defined]
        del self._remove_confirm_sessions  # type: ignore[attr-defined]

        if answer in ("yes", "y"):
            # Detach all active sessions
            for s in sessions:
                try:
                    if s.status == "active":
                        self.router.detach(s.channel_id)
                except Exception as e:
                    logger.warning(f"Failed to detach session {s.daemon_session_id}: {e}")

            await self._do_remove_machine(channel_id, machine_id)
        else:
            await self.send_message(channel_id, f"Removal of **{machine_id}** cancelled.")

        return True

    async def _do_remove_machine(self, channel_id: str, machine_id: str) -> None:
        """Actually remove a machine from config and runtime."""
        # Close tunnel if exists
        if machine_id in self.ssh.tunnels:
            tunnel = self.ssh.tunnels[machine_id]
            await tunnel.close()
            del self.ssh.tunnels[machine_id]

        # Remove from runtime config
        del self.config.machines[machine_id]

        # Remove from config.yaml
        try:
            remove_machine_from_config(self.config, machine_id)
        except Exception as e:
            logger.warning(f"Failed to remove from config.yaml: {e}")
            await self.send_message(channel_id, f"**Warning:** Removed from runtime but failed to update config.yaml: {e}")

        await self.send_message(channel_id, f"Machine **{machine_id}** removed.")

    async def cmd_restart(self, channel_id: str, user_id: Optional[int] = None) -> None:
        """/restart - Restart the head node process (admin only)."""
        if not self.is_admin(user_id):
            await self.send_message(channel_id, "**Error:** `/restart` requires admin privileges.")
            return

        await self.send_message(channel_id, "Restarting head node...")
        logger.info(f"Restart requested by user {user_id}")

        # Give Discord a moment to deliver the message
        await asyncio.sleep(1)

        # Perform os.execv to replace this process with a fresh copy
        self._do_restart()

    async def cmd_update(self, channel_id: str, user_id: Optional[int] = None) -> None:
        """/update - Git pull and restart (admin only)."""
        if not self.is_admin(user_id):
            await self.send_message(channel_id, "**Error:** `/update` requires admin privileges.")
            return

        project_dir = str(Path(__file__).resolve().parent.parent)
        await self.send_message(channel_id, "Pulling latest code...")

        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            await self.send_message(channel_id, format_error("Git pull timed out after 30s."))
            return
        except FileNotFoundError:
            await self.send_message(channel_id, format_error("Git not found on this machine."))
            return

        if result.returncode != 0:
            stderr = result.stderr.strip()[:500] if result.stderr else "(no output)"
            await self.send_message(
                channel_id,
                format_error(f"Git pull failed:\n```\n{stderr}\n```")
            )
            return

        # Show what changed
        stdout = result.stdout.strip() if result.stdout else "(no output)"
        if "Already up to date" in stdout:
            await self.send_message(channel_id, f"Already up to date. No restart needed.\n```\n{stdout}\n```")
            return

        await self.send_message(channel_id, f"Updated:\n```\n{stdout}\n```\nRestarting...")
        logger.info(f"Update requested by user {user_id}: {stdout}")

        await asyncio.sleep(1)
        self._do_restart()

    @staticmethod
    def _do_restart() -> None:
        """Replace this process with a fresh copy via os.execv."""
        from . import main as main_module
        exe = main_module._startup_executable
        config_path = main_module._startup_config_path
        workdir = main_module._startup_workdir

        args = [exe, "-m", "head.main", config_path]
        logger.info(f"Restarting: {' '.join(args)} (cwd={workdir})")

        # Restore working directory (in case it changed)
        os.chdir(workdir)
        os.execv(exe, args)

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
`/add-machine <name>` - Add machine (from SSH config)
`/add-machine --from-ssh` - Browse all SSH hosts
`/remove-machine <machine>` - Remove a machine
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
