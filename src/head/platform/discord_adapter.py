"""
Discord adapter implementing PlatformAdapter protocol.

Uses discord.py (v2) with:
- Slash commands (app_commands) for autocomplete popups
- Typing indicator during Claude processing
- 30s heartbeat status updates to avoid Discord 3-min timeout

Phase 1: preserves all existing DiscordBot behaviour while conforming
to the PlatformAdapter interface so the engine layer can drive it.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from head.config import Config, DiscordConfig
from head.file_pool import FilePool, FileEntry
from head.message_formatter import (
    split_message,
    compress_tool_messages,
    format_activity_message,
    format_tool_line,
    format_error,
    display_mode,
)
from head.platform.protocol import (
    PlatformAdapter,
    MessageHandle,
    FileAttachment,
    InputHandler,
)

if TYPE_CHECKING:
    # Avoid circular import; engine reference only used for _forward_message_with_heartbeat
    from head.engine import BotEngine as EngineType

logger = logging.getLogger(__name__)

# Heartbeat interval for status updates (seconds)
HEARTBEAT_INTERVAL = 25
# Stream update interval (seconds)
STREAM_UPDATE_INTERVAL = 1.5
# Max buffer before flushing to a new message
STREAM_BUFFER_FLUSH_SIZE = 1800


class DiscordAdapter:
    """
    Discord adapter implementing PlatformAdapter protocol.

    Wraps all Discord-specific I/O: bot setup, slash commands,
    typing indicators, heartbeat, deferred interactions, and channel caching.

    Command logic is NOT implemented here; the adapter delegates to the
    engine (a BotBase subclass) via the InputHandler callback set by
    set_input_handler().

    For regular (non-command) messages, the adapter calls
    _forward_message_with_heartbeat() which accesses the engine's internals
    to preserve the heartbeat status-update behaviour that is specific to
    Discord's 3-minute inactivity timeout.
    """

    def __init__(
        self,
        config: Config,
        file_pool: Optional[FilePool] = None,
    ) -> None:
        self.config = config
        self.file_pool = file_pool
        self._discord_config: Optional[DiscordConfig] = config.bot.discord

        if not self._discord_config:
            raise ValueError("Discord config not found in config.yaml")

        # --- discord.py bot ---
        intents = discord.Intents.default()
        intents.message_content = True

        self.bot = commands.Bot(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        # --- Internal state ---
        # channel_id -> discord channel object
        self._channels: dict[str, discord.abc.Messageable] = {}
        # channel_id -> active typing task
        self._typing_tasks: dict[str, asyncio.Task] = {}
        # channel_id -> heartbeat status message
        self._heartbeat_msgs: dict[str, discord.Message] = {}
        # channel_id -> pending deferred interaction (first send_message consumes it)
        self._deferred_interactions: dict[str, discord.Interaction] = {}
        # session daemon IDs that have already shown the "Connected to" init message
        self._init_shown: set[str] = set()
        # channels currently streaming (prevent concurrent forwarding)
        self._streaming: set[str] = set()

        # --- Callbacks / engine reference ---
        self._on_input: Optional[InputHandler] = None
        # The engine (BotBase) is needed for _forward_message_with_heartbeat
        self._engine: Optional[Any] = None

        self._setup_events()
        self._setup_slash_commands()

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – properties
    # -----------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "discord"

    @property
    def max_message_length(self) -> int:
        return 2000

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – capability queries
    # -----------------------------------------------------------------------

    def supports_message_edit(self) -> bool:
        return True

    def supports_inline_buttons(self) -> bool:
        return False

    def supports_file_upload(self) -> bool:
        return True

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – input handler
    # -----------------------------------------------------------------------

    def set_input_handler(self, handler: InputHandler) -> None:
        """Set the callback invoked when a user message arrives."""
        self._on_input = handler

    def set_engine(self, engine: Any) -> None:
        """
        Store a reference to the engine (BotBase) so that
        _forward_message_with_heartbeat can access engine internals
        (router, ssh, daemon, config) for the streaming/heartbeat loop.
        """
        self._engine = engine

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – message operations
    # -----------------------------------------------------------------------

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """
        Send a message to a Discord channel.

        If there is a pending deferred interaction for this channel,
        use interaction.followup.send() to complete it properly.
        Returns a MessageHandle wrapping the discord.Message.
        """
        interaction = self._deferred_interactions.pop(channel_id, None)
        if interaction:
            try:
                chunks = split_message(text, max_len=2000)
                last_msg: Optional[discord.Message] = None
                for chunk in chunks:
                    last_msg = await interaction.followup.send(chunk, wait=True)
                if last_msg:
                    return MessageHandle(
                        platform="discord",
                        channel_id=channel_id,
                        message_id=str(last_msg.id),
                        raw=last_msg,
                    )
            except discord.HTTPException as e:
                logger.warning(f"Failed to send followup, falling back to channel.send: {e}")
                # Fall through to normal send

        channel = self._channels.get(channel_id)
        if not channel:
            logger.warning(f"Channel not found: {channel_id}")
            return MessageHandle(
                platform="discord",
                channel_id=channel_id,
                message_id="0",
            )

        chunks = split_message(text, max_len=2000)
        last_msg = None

        for chunk in chunks:
            try:
                last_msg = await channel.send(chunk)
            except discord.HTTPException as e:
                logger.error(f"Failed to send message: {e}")
                try:
                    plain = chunk.replace("**", "").replace("`", "").replace("```", "")
                    last_msg = await channel.send(plain[:2000])
                except discord.HTTPException:
                    logger.error(f"Failed to send even plain message to {channel_id}")

        if last_msg:
            return MessageHandle(
                platform="discord",
                channel_id=channel_id,
                message_id=str(last_msg.id),
                raw=last_msg,
            )
        return MessageHandle(
            platform="discord",
            channel_id=channel_id,
            message_id="0",
        )

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit an existing Discord message using its MessageHandle."""
        msg = handle.raw
        if not isinstance(msg, discord.Message):
            return

        try:
            if len(text) > 2000:
                text = text[:1997] + "..."
            await msg.edit(content=text)
        except discord.HTTPException as e:
            logger.warning(f"Failed to edit message: {e}")
            try:
                channel = self._channels.get(handle.channel_id)
                if channel:
                    await channel.send(text[:2000])
            except Exception:
                pass
        except discord.NotFound:
            pass

    async def delete_message(self, handle: MessageHandle) -> None:
        """Delete an existing Discord message."""
        msg = handle.raw
        if not isinstance(msg, discord.Message):
            return
        try:
            await msg.delete()
        except (discord.HTTPException, discord.NotFound) as e:
            logger.debug(f"Failed to delete message: {e}")

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – file operations
    # -----------------------------------------------------------------------

    async def download_file(self, attachment: FileAttachment, dest: Path) -> Path:
        """Download a Discord attachment URL to a local path."""
        if not attachment.url:
            raise ValueError(f"No URL for attachment {attachment.filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                resp.raise_for_status()
                dest.write_bytes(await resp.read())
        return dest

    async def send_file(self, channel_id: str, path: Path, caption: str = "") -> MessageHandle:
        """Send a file to a Discord channel."""
        channel = self._channels.get(channel_id)
        if not channel:
            logger.warning(f"Channel not found for send_file: {channel_id}")
            return MessageHandle(
                platform="discord",
                channel_id=channel_id,
                message_id="0",
            )
        try:
            discord_file = discord.File(str(path), filename=path.name)
            msg = await channel.send(
                content=caption if caption else None,
                file=discord_file,
            )
            return MessageHandle(
                platform="discord",
                channel_id=channel_id,
                message_id=str(msg.id),
                raw=msg,
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send file to {channel_id}: {e}")
            return MessageHandle(
                platform="discord",
                channel_id=channel_id,
                message_id="0",
            )

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – typing indicator
    # -----------------------------------------------------------------------

    async def start_typing(self, channel_id: str) -> None:
        """Start the Discord typing indicator in the channel."""
        await self._start_typing(channel_id)

    async def stop_typing(self, channel_id: str) -> None:
        """Stop the Discord typing indicator in the channel."""
        await self._stop_typing(channel_id)

    # -----------------------------------------------------------------------
    # PlatformAdapter protocol – lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to Discord and start listening."""
        if not self._discord_config:
            raise ValueError("Discord config not set")

        token = self._discord_config.token
        if not token:
            raise ValueError("Discord token is empty. Set DISCORD_TOKEN environment variable.")

        logger.info("Starting Discord adapter...")
        await self.bot.start(token)

    async def stop(self) -> None:
        """Disconnect from Discord and clean up."""
        logger.info("Stopping Discord adapter...")
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        await self.bot.close()

    # -----------------------------------------------------------------------
    # Restart notification
    # -----------------------------------------------------------------------

    async def check_restart_notify(self) -> None:
        """Check for restart notification file and send message to the channel."""
        restart_file = Path.cwd() / ".restart_notify"
        if not restart_file.exists():
            return
        try:
            content = restart_file.read_text().strip().splitlines()
            restart_file.unlink()
            if len(content) >= 2:
                channel_id = content[0]  # e.g. "discord:123456"
                reason = content[1]
                # Extract numeric ID and fetch the channel object
                if channel_id.startswith("discord:"):
                    discord_id = int(channel_id.split(":", 1)[1])
                    channel = self.bot.get_channel(discord_id)
                    if channel is None:
                        channel = await self.bot.fetch_channel(discord_id)
                    if channel:
                        self._channels[channel_id] = channel
                        await self.send_message(
                            channel_id,
                            f"**{reason} complete.** Head node is back online.",
                        )
        except Exception as e:
            logger.warning(f"Failed to process restart notify: {e}")

    # -----------------------------------------------------------------------
    # Internal: Discord event setup
    # -----------------------------------------------------------------------

    def _setup_events(self) -> None:
        """Register Discord event handlers."""

        @self.bot.event
        async def on_ready() -> None:
            logger.info(f"Discord adapter logged in as {self.bot.user}")
            if self.bot.user:
                logger.info(f"Bot ID: {self.bot.user.id}")
            try:
                synced = await self.bot.tree.sync()
                logger.info(f"Synced {len(synced)} slash command(s)")
            except Exception as e:
                logger.error(f"Failed to sync slash commands: {e}")

            await self.check_restart_notify()

        @self.bot.event
        async def on_message(message: discord.Message) -> None:
            # Ignore bot messages
            if message.author == self.bot.user:
                return
            if message.author.bot:
                return

            # Check allowed channels
            if self._discord_config and self._discord_config.allowed_channels:
                if message.channel.id not in self._discord_config.allowed_channels:
                    return

            channel_id = f"discord:{message.channel.id}"
            self._channels[channel_id] = message.channel

            # Text-based slash commands go through handle_input
            if message.content.startswith("/"):
                if self._on_input:
                    await self._on_input(channel_id, message.content, message.author.id, None)
                return

            # Process file attachments
            file_refs: list[FileEntry] = []
            platform_attachments: list[FileAttachment] = []

            if message.attachments and self.file_pool:
                # Resolve session prefix for FilePool naming
                session = None
                if self._engine:
                    session = self._engine.router.resolve(channel_id)
                session_prefix = session.daemon_session_id[:8] if session else "nosess"

                for att in message.attachments:
                    if not self.file_pool.is_allowed_type(att.filename, att.content_type):
                        try:
                            await message.channel.send(
                                f"Skipping unsupported file: `{att.filename}` ({att.content_type})"
                            )
                        except Exception:
                            pass
                        continue
                    try:
                        entry = await self.file_pool.download_discord_attachment(att, session_prefix=session_prefix)
                        file_refs.append(entry)
                        # Also build a FileAttachment for the protocol callback
                        platform_attachments.append(
                            FileAttachment(
                                filename=att.filename,
                                size=att.size,
                                mime_type=att.content_type,
                                url=att.url,
                                platform_ref=att,
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Failed to download attachment {att.filename}: {e}")
                        try:
                            await message.channel.send(f"Failed to download `{att.filename}`: {e}")
                        except Exception:
                            pass

            # Build text with file markers (legacy format used by engine)
            text = message.content or ""
            if file_refs:
                for ref in file_refs:
                    text += f"\n<discord_file>{ref.file_id}</discord_file>"

            if not text.strip() and not file_refs:
                return

            # Non-command messages: use heartbeat forwarding if engine is set,
            # otherwise fall back to the plain input handler.
            if self._engine:
                await self._forward_message_with_heartbeat(channel_id, text, file_refs=file_refs)
            elif self._on_input:
                await self._on_input(
                    channel_id,
                    text,
                    message.author.id,
                    platform_attachments if platform_attachments else None,
                )

    # -----------------------------------------------------------------------
    # Internal: deferred interaction helper
    # -----------------------------------------------------------------------

    def _defer_and_register(self, interaction: discord.Interaction) -> str:
        """Register a deferred interaction and return the channel_id string."""
        channel_id = f"discord:{interaction.channel_id}"
        self._channels[channel_id] = interaction.channel
        self._deferred_interactions[channel_id] = interaction
        return channel_id

    # -----------------------------------------------------------------------
    # Internal: typing indicator
    # -----------------------------------------------------------------------

    async def _start_typing(self, channel_id: str) -> None:
        """Start showing 'bot is typing...' indicator in the channel."""
        channel = self._channels.get(channel_id)
        if not channel:
            return

        async def typing_loop() -> None:
            try:
                while True:
                    await channel.typing()
                    await asyncio.sleep(8)  # Discord typing indicator lasts ~10s
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Typing indicator error: {e}")

        task = asyncio.create_task(typing_loop())
        self._typing_tasks[channel_id] = task

    async def _stop_typing(self, channel_id: str) -> None:
        """Cancel and remove the typing indicator task for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # -----------------------------------------------------------------------
    # Internal: heartbeat
    # -----------------------------------------------------------------------

    async def _heartbeat_loop(self, channel_id: str, start_time: float, event_tracker: dict) -> None:
        """
        Send periodic status updates every HEARTBEAT_INTERVAL seconds to avoid
        Discord's 3-minute inactivity feeling.

        event_tracker is a mutable dict shared with the streaming coroutine:
            - "last_event_type": type of last event received
            - "tool_name": current tool being used (if any)
            - "done": whether streaming is complete
            - "partial_len": length of accumulated partial content
        """
        try:
            while not event_tracker.get("done", False):
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if event_tracker.get("done", False):
                    break

                elapsed = int(time.time() - start_time)
                mins, secs = divmod(elapsed, 60)

                last_type = event_tracker.get("last_event_type", "")
                tool_name = event_tracker.get("tool_name", "")
                partial_len = event_tracker.get("partial_len", 0)

                if tool_name:
                    status_text = f"Using tool: **{tool_name}**"
                elif last_type == "partial" and partial_len > 0:
                    status_text = "Writing response..."
                elif last_type in ("tool_use", "tool_result"):
                    status_text = "Processing tool results..."
                else:
                    status_text = "Thinking..."

                time_str = f"{mins}m{secs:02d}s" if mins > 0 else f"{secs}s"
                heartbeat_text = f"`[{time_str}]` Claude is working... {status_text}"

                channel = self._channels.get(channel_id)
                if not channel:
                    break

                try:
                    existing = self._heartbeat_msgs.get(channel_id)
                    if existing:
                        await existing.edit(content=heartbeat_text)
                    else:
                        msg = await channel.send(heartbeat_text)
                        self._heartbeat_msgs[channel_id] = msg
                except discord.HTTPException as e:
                    logger.debug(f"Heartbeat message error: {e}")
                    try:
                        msg = await channel.send(heartbeat_text)
                        self._heartbeat_msgs[channel_id] = msg
                    except Exception:
                        pass

        except asyncio.CancelledError:
            pass
        finally:
            msg = self._heartbeat_msgs.pop(channel_id, None)
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Internal: message forwarding with heartbeat (Discord-specific)
    # -----------------------------------------------------------------------

    async def _forward_message_with_heartbeat(self, channel_id: str, text: str, file_refs: list | None = None) -> None:
        """
        Forward a user message to Claude with typing indicator, heartbeat, and
        optional file upload.

        This method lives here (not in the engine) because the heartbeat
        behaviour is Discord-specific: it posts and edits a status message
        every HEARTBEAT_INTERVAL seconds using Discord channel objects.

        The method accesses the engine's router, ssh, daemon, and config
        attributes via self._engine.
        """
        engine = self._engine
        if engine is None:
            logger.error("_forward_message_with_heartbeat called with no engine set")
            return

        session = engine.router.resolve(channel_id)
        if not session:
            await self.send_message(channel_id, "No active session. Use `/start` to begin.")
            return

        # Prevent concurrent streaming
        if channel_id in self._streaming:
            await self.send_message(channel_id, "Claude is still processing. Please wait...")
            return

        self._streaming.add(channel_id)

        # Shared state between streaming loop and heartbeat task
        event_tracker: dict = {
            "last_event_type": "",
            "tool_name": "",
            "done": False,
            "partial_len": 0,
        }

        await self._start_typing(channel_id)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(channel_id, time.time(), event_tracker))

        try:
            from head.daemon_client import DaemonConnectionError

            local_port = await engine.ssh.ensure_tunnel(session.machine_id)

            # Upload files and replace markers before sending to Claude
            if file_refs:
                try:
                    text = await engine._upload_and_replace_files(session.machine_id, text, file_refs)
                except Exception as e:
                    await self.send_message(channel_id, format_error(f"File upload failed: {e}"))
                    return

            # Activity message state (tools + thinking)
            activity_msg: Optional[MessageHandle] = None
            activity_lines: list[str] = []
            thinking_buf: str = ""
            last_activity_update = time.time()

            STREAM_UPDATE_INTERVAL = 1.5

            async def update_activity():
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
                nonlocal activity_msg, activity_lines
                if activity_msg is not None and activity_lines:
                    final = format_activity_message(activity_lines, "", cursor=False)
                    await self.edit_message(activity_msg, final)
                activity_msg = None
                activity_lines = []

            async for event in engine.daemon.send_message(local_port, session.daemon_session_id, text):
                event_type = event.get("type", "")
                event_tracker["last_event_type"] = event_type

                if event_type == "ping":
                    continue

                if event_type == "tool_use":
                    tool_name = event.get("tool", "unknown")
                    event_tracker["tool_name"] = tool_name
                    activity_lines.append(format_tool_line(event))
                    thinking_buf = ""
                    await update_activity()

                elif event_type == "partial":
                    content = event.get("content", "")
                    if content:
                        thinking_buf += content
                        event_tracker["partial_len"] = len(thinking_buf)
                        now = time.time()
                        if now - last_activity_update >= STREAM_UPDATE_INTERVAL:
                            await update_activity()

                elif event_type == "text":
                    content = event.get("content", "")
                    if content:
                        thinking_buf = ""
                        await finalize_activity()
                        for chunk in split_message(content):
                            await self.send_message(channel_id, chunk)
                    event_tracker["partial_len"] = 0

                elif event_type == "result":
                    sdk_session_id = event.get("session_id")
                    if sdk_session_id:
                        engine.router.update_sdk_session(channel_id, sdk_session_id)

                elif event_type == "system":
                    model = event.get("model")
                    if model and event.get("subtype") == "init":
                        current_session = engine.router.resolve(channel_id)
                        daemon_sid = current_session.daemon_session_id if current_session else ""
                        if daemon_sid not in self._init_shown:
                            self._init_shown.add(daemon_sid)
                            mode_str = display_mode(current_session.mode) if current_session else "unknown"
                            await self.send_message(
                                channel_id,
                                f"Connected to **{model}** | Mode: **{mode_str}**",
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

            # Finalize any remaining activity message
            thinking_buf = ""
            await finalize_activity()

        except DaemonConnectionError as e:
            await self.send_message(channel_id, format_error(f"Lost connection to daemon: {e}"))
        except Exception as e:
            logger.exception("Error forwarding message to Claude")
            await self.send_message(channel_id, format_error(f"Unexpected error: {e}"))
        finally:
            event_tracker["done"] = True
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._stop_typing(channel_id)
            self._streaming.discard(channel_id)

    # -----------------------------------------------------------------------
    # Internal: slash command setup (all 17 commands + autocomplete)
    # -----------------------------------------------------------------------

    def _setup_slash_commands(self) -> None:
        """Register all 17 Discord slash commands with autocomplete."""
        tree = self.bot.tree

        # ------------------------------------------------------------------ /start
        @tree.command(name="start", description="Start a new Claude session on a remote machine")
        @app_commands.describe(
            machine="Remote machine ID (e.g. dice-fuji1)",
            path="Project path on the remote machine",
        )
        async def slash_start(interaction: discord.Interaction, machine: str, path: str) -> None:
            channel_id = f"discord:{interaction.channel_id}"
            self._channels[channel_id] = interaction.channel
            await interaction.response.send_message(f"\u26a1 Starting session on **{machine}**:`{path}`...")
            if self._engine:
                try:
                    # Call engine directly with silent_init=True to avoid duplicate message
                    await self._engine.cmd_start(channel_id, [machine, path], silent_init=True)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_start.autocomplete("machine")
        async def start_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        @slash_start.autocomplete("path")
        async def start_path_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            machine_id = None
            if interaction.namespace and hasattr(interaction.namespace, "machine"):
                machine_id = interaction.namespace.machine
            paths: list[str] = []
            if machine_id and machine_id in self.config.machines:
                paths = self.config.machines[machine_id].default_paths
            elif self.config.machines:
                for mc in self.config.machines.values():
                    paths.extend(mc.default_paths)
                paths = list(set(paths))
            return [app_commands.Choice(name=p, value=p) for p in paths if current.lower() in p.lower()][:25]

        # ------------------------------------------------------------------ /resume
        @tree.command(name="resume", description="Resume a previously detached session")
        @app_commands.describe(session_id="Session ID or name to resume")
        async def slash_resume(interaction: discord.Interaction, session_id: str) -> None:
            channel_id = f"discord:{interaction.channel_id}"
            self._channels[channel_id] = interaction.channel
            await interaction.response.send_message(f"Resuming session `{session_id}`...")
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/resume {session_id}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /ls
        @tree.command(name="ls", description="List machines or sessions")
        @app_commands.describe(
            target="What to list: 'machine' or 'session'",
            machine="Filter sessions by machine (optional)",
        )
        @app_commands.choices(
            target=[
                app_commands.Choice(name="machine", value="machine"),
                app_commands.Choice(name="session", value="session"),
            ]
        )
        async def slash_ls(
            interaction: discord.Interaction,
            target: app_commands.Choice[str],
            machine: Optional[str] = None,
        ) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            cmd = f"/ls {target.value}"
            if machine:
                cmd += f" {machine}"
            if self._on_input:
                try:
                    await self._on_input(channel_id, cmd, interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_ls.autocomplete("machine")
        async def ls_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /exit
        @tree.command(
            name="exit",
            description="Detach from current Claude session (doesn't destroy it)",
        )
        async def slash_exit(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/exit", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /rm
        @tree.command(name="rm", description="Destroy a session on a machine")
        @app_commands.describe(
            machine="Remote machine ID",
            path="Project path of the session to destroy",
        )
        async def slash_rm(interaction: discord.Interaction, machine: str, path: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/rm {machine} {path}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_rm.autocomplete("machine")
        async def rm_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /rm-session
        @tree.command(name="rm-session", description="Destroy a specific session by name or ID")
        @app_commands.describe(session="Session name or ID")
        async def slash_rm_session(interaction: discord.Interaction, session: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/rm-session {session}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_rm_session.autocomplete("session")
        async def rm_session_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            router = self._engine.router if self._engine else None
            sessions = router.list_sessions() if router else []
            choices = []
            for s in sessions:
                if s.status in ("active", "detached"):
                    label = s.name or s.daemon_session_id[:8]
                    value = s.name or s.daemon_session_id
                    display = f"{label} ({s.machine_id}:{s.path})"
                    if current.lower() in display.lower():
                        choices.append(app_commands.Choice(name=display[:100], value=value))
            return choices[:25]

        # ------------------------------------------------------------------ /mode
        @tree.command(name="mode", description="Switch Claude permission mode")
        @app_commands.describe(mode="Permission mode")
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="bypass - Full auto (skip all permissions)", value="auto"),
                app_commands.Choice(name="code - Auto accept edits, confirm bash", value="code"),
                app_commands.Choice(name="plan - Read-only analysis", value="plan"),
                app_commands.Choice(name="ask - Confirm everything", value="ask"),
            ]
        )
        async def slash_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/mode {mode.value}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /tool-display
        @tree.command(name="tool-display", description="Switch tool display mode")
        @app_commands.describe(mode="Tool display mode")
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="append - Show each tool call progressively", value="append"),
                app_commands.Choice(name="batch - Show tool summary at end", value="batch"),
            ]
        )
        async def slash_tool_display(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/tool-display {mode.value}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /status
        @tree.command(name="status", description="Show current session info and queue stats")
        async def slash_status(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/status", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /rename
        @tree.command(name="rename", description="Rename the current session")
        @app_commands.describe(name="New name for the session (e.g. my-project)")
        async def slash_rename(interaction: discord.Interaction, name: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, f"/rename {name}", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /interrupt
        @tree.command(name="interrupt", description="Interrupt Claude's current operation")
        async def slash_interrupt(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/interrupt", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /new
        @tree.command(name="new", description="Start a fresh session in the same location (destroy current)")
        async def slash_new(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/new", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /help
        @tree.command(name="help", description="Show available Codecast commands")
        async def slash_help(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/help", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /health
        @tree.command(name="health", description="Check daemon health status")
        @app_commands.describe(machine="Remote machine ID (optional, defaults to current session or all)")
        async def slash_health(interaction: discord.Interaction, machine: Optional[str] = None) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            cmd = f"/health {machine}" if machine else "/health"
            if self._on_input:
                try:
                    await self._on_input(channel_id, cmd, interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_health.autocomplete("machine")
        async def health_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /monitor
        @tree.command(name="monitor", description="Monitor session details and queues")
        @app_commands.describe(machine="Remote machine ID (optional, defaults to current session or all)")
        async def slash_monitor(interaction: discord.Interaction, machine: Optional[str] = None) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            cmd = f"/monitor {machine}" if machine else "/monitor"
            if self._on_input:
                try:
                    await self._on_input(channel_id, cmd, interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_monitor.autocomplete("machine")
        async def monitor_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /add-machine
        @tree.command(
            name="add-machine",
            description="Add a machine (auto-resolves from SSH config)",
        )
        @app_commands.describe(
            machine_id="Machine name (if in SSH config, host/user auto-filled)",
            host="Hostname or IP (optional if in SSH config)",
            user="SSH username (optional if in SSH config)",
            proxy_jump="Jump host machine ID (optional)",
            node_path="Path to node binary on remote (optional)",
            daemon_port="Daemon port (default: 9100)",
            paths="Comma-separated default project paths (optional)",
        )
        async def slash_add_machine(
            interaction: discord.Interaction,
            machine_id: str,
            host: Optional[str] = None,
            user: Optional[str] = None,
            proxy_jump: Optional[str] = None,
            node_path: Optional[str] = None,
            daemon_port: Optional[int] = 9100,
            paths: Optional[str] = None,
        ) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            # Build a text command that handle_input can parse
            args_list: list[str] = []
            if host and user:
                args_list = [machine_id, host, user]
            else:
                args_list = [machine_id]
            if proxy_jump:
                args_list.extend(["--proxy-jump", proxy_jump])
            if node_path:
                args_list.extend(["--node-path", node_path])
            if daemon_port and daemon_port != 9100:
                args_list.extend(["--daemon-port", str(daemon_port)])
            if paths:
                args_list.extend(["--paths", paths])
            cmd = "/add-machine " + " ".join(args_list)
            if self._on_input:
                try:
                    await self._on_input(channel_id, cmd, interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_add_machine.autocomplete("machine_id")
        async def add_machine_id_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            from head.config import parse_ssh_config

            existing = set(self.config.machines.keys())
            ssh_hosts = [
                e.name for e in parse_ssh_config() if e.name not in existing and current.lower() in e.name.lower()
            ]
            return [app_commands.Choice(name=h, value=h) for h in ssh_hosts][:25]

        @slash_add_machine.autocomplete("proxy_jump")
        async def add_machine_proxy_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            machines = [mid for mid in self.config.machines if current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /import-ssh
        @tree.command(name="import-ssh", description="Import machines from SSH config")
        async def slash_import_ssh(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/add-machine --from-ssh", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /remove-machine
        @tree.command(name="remove-machine", description="Remove a machine from config")
        @app_commands.describe(machine="Machine ID to remove")
        async def slash_remove_machine(interaction: discord.Interaction, machine: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(
                        channel_id,
                        f"/remove-machine {machine}",
                        interaction.user.id,
                        None,
                    )
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        @slash_remove_machine.autocomplete("machine")
        async def remove_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [mid for mid in self.config.machines if mid not in jump_hosts and current.lower() in mid.lower()]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        # ------------------------------------------------------------------ /update
        @tree.command(name="update", description="Pull latest code and restart (admin only)")
        async def slash_update(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/update", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))

        # ------------------------------------------------------------------ /restart
        @tree.command(name="restart", description="Restart the head node (admin only)")
        async def slash_restart(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            if self._on_input:
                try:
                    await self._on_input(channel_id, "/restart", interaction.user.id, None)
                except Exception as e:
                    await self.send_message(channel_id, format_error(str(e)))
