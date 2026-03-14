"""
Discord Bot implementation for Remote Claude.

Uses discord.py (v2) with:
- Slash commands (app_commands) for autocomplete popups
- Typing indicator during Claude processing
- 30s heartbeat status updates to avoid Discord 3-min timeout
"""

import asyncio
import logging
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from .config import Config, DiscordConfig
from .ssh_manager import SSHManager
from .session_router import SessionRouter
from .daemon_client import DaemonClient, DaemonError, DaemonConnectionError
from .bot_base import BotBase
from .message_formatter import split_message, compress_tool_messages, format_error, display_mode
from .file_pool import FilePool, FileEntry

logger = logging.getLogger(__name__)

# Heartbeat interval for status updates (seconds)
HEARTBEAT_INTERVAL = 25
# Stream update interval (seconds)
STREAM_UPDATE_INTERVAL = 1.5
# Max buffer before flushing to a new message
STREAM_BUFFER_FLUSH_SIZE = 1800


class DiscordBot(BotBase):
    """Discord bot implementation with slash commands and heartbeat."""

    def __init__(
        self,
        ssh_manager: SSHManager,
        session_router: SessionRouter,
        daemon_client: DaemonClient,
        config: Config,
        file_pool: Optional[FilePool] = None,
    ):
        super().__init__(ssh_manager, session_router, daemon_client, config)
        self.discord_config: Optional[DiscordConfig] = config.bot.discord
        self.file_pool = file_pool

        if not self.discord_config:
            raise ValueError("Discord config not found in config.yaml")

        # Set up discord intents
        intents = discord.Intents.default()
        intents.message_content = True

        self.bot = commands.Bot(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        # Store channel objects for sending messages
        self._channels: dict[str, discord.abc.Messageable] = {}
        # Track active typing tasks per channel
        self._typing_tasks: dict[str, asyncio.Task] = {}
        # Track heartbeat status messages per channel
        self._heartbeat_msgs: dict[str, discord.Message] = {}
        # Pending deferred interactions: channel_id -> interaction
        # The first send_message call for this channel will use interaction.followup.send()
        self._deferred_interactions: dict[str, discord.Interaction] = {}
        # Track which sessions have already shown the "Connected to" init message
        self._init_shown: set[str] = set()

        self._setup_events()
        self._setup_slash_commands()

    def _setup_events(self) -> None:
        """Register Discord event handlers."""

        @self.bot.event
        async def on_ready() -> None:
            logger.info(f"Discord bot logged in as {self.bot.user}")
            if self.bot.user:
                logger.info(f"Bot ID: {self.bot.user.id}")
            # Sync slash commands to all guilds
            try:
                synced = await self.bot.tree.sync()
                logger.info(f"Synced {len(synced)} slash command(s)")
            except Exception as e:
                logger.error(f"Failed to sync slash commands: {e}")

        @self.bot.event
        async def on_message(message: discord.Message) -> None:
            # Ignore bot's own messages
            if message.author == self.bot.user:
                return
            # Ignore messages from other bots
            if message.author.bot:
                return
            # Ignore slash commands (handled by app_commands)
            if message.content.startswith("/"):
                return
            # Check if channel is allowed
            if self.discord_config and self.discord_config.allowed_channels:
                if message.channel.id not in self.discord_config.allowed_channels:
                    return

            channel_id = f"discord:{message.channel.id}"
            self._channels[channel_id] = message.channel

            # Process attachments
            file_refs: list[FileEntry] = []
            if message.attachments and self.file_pool:
                session = self.router.resolve(channel_id)
                session_prefix = session.daemon_session_id[:8] if session else "nosess"

                for att in message.attachments:
                    if not self.file_pool.is_allowed_type(att.filename, att.content_type):
                        await message.channel.send(
                            f"Skipping unsupported file: `{att.filename}` ({att.content_type})"
                        )
                        continue
                    try:
                        entry = await self.file_pool.download_discord_attachment(
                            att, session_prefix=session_prefix
                        )
                        file_refs.append(entry)
                    except Exception as e:
                        logger.warning(f"Failed to download attachment {att.filename}: {e}")
                        await message.channel.send(f"Failed to download `{att.filename}`: {e}")

            # Build message with file markers
            text = message.content or ""
            if file_refs:
                for ref in file_refs:
                    text += f"\n<discord_file>{ref.file_id}</discord_file>"

            # Skip if no content and no attachments
            if not text.strip() and not file_refs:
                return

            # Forward non-command messages to Claude session
            await self._forward_message_with_heartbeat(channel_id, text, file_refs=file_refs)

    def _defer_and_register(self, interaction: discord.Interaction) -> str:
        """Register a deferred interaction and return channel_id."""
        channel_id = f"discord:{interaction.channel_id}"
        self._channels[channel_id] = interaction.channel
        self._deferred_interactions[channel_id] = interaction
        return channel_id

    def _setup_slash_commands(self) -> None:
        """Register Discord slash commands with autocomplete."""
        tree = self.bot.tree

        @tree.command(name="start", description="Start a new Claude session on a remote machine")
        @app_commands.describe(
            machine="Remote machine ID (e.g. dice-fuji1)",
            path="Project path on the remote machine",
        )
        async def slash_start(interaction: discord.Interaction, machine: str, path: str) -> None:
            channel_id = f"discord:{interaction.channel_id}"
            self._channels[channel_id] = interaction.channel
            await interaction.response.send_message(
                f"Starting session on **{machine}**:`{path}`..."
            )
            try:
                await self.cmd_start(channel_id, [machine, path], silent_init=True)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @slash_start.autocomplete("machine")
        async def start_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            # Exclude jump hosts (those without default_paths or used as proxy_jump)
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [
                mid for mid in self.config.machines
                if mid not in jump_hosts and current.lower() in mid.lower()
            ]
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
            return [
                app_commands.Choice(name=p, value=p)
                for p in paths
                if current.lower() in p.lower()
            ][:25]

        @tree.command(name="resume", description="Resume a previously detached session")
        @app_commands.describe(session_id="Session ID or name to resume")
        async def slash_resume(interaction: discord.Interaction, session_id: str) -> None:
            channel_id = f"discord:{interaction.channel_id}"
            self._channels[channel_id] = interaction.channel
            await interaction.response.send_message(f"Resuming session `{session_id}`...")
            try:
                await self.cmd_resume(channel_id, [session_id])
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="ls", description="List machines or sessions")
        @app_commands.describe(target="What to list: 'machine' or 'session'", machine="Filter sessions by machine (optional)")
        @app_commands.choices(target=[
            app_commands.Choice(name="machine", value="machine"),
            app_commands.Choice(name="session", value="session"),
        ])
        async def slash_ls(
            interaction: discord.Interaction,
            target: app_commands.Choice[str],
            machine: Optional[str] = None,
        ) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            args = [target.value]
            if machine:
                args.append(machine)
            try:
                await self.cmd_ls(channel_id, args)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @slash_ls.autocomplete("machine")
        async def ls_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [
                mid for mid in self.config.machines
                if mid not in jump_hosts and current.lower() in mid.lower()
            ]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        @tree.command(name="exit", description="Detach from current Claude session (doesn't destroy it)")
        async def slash_exit(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_exit(channel_id)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="rm", description="Destroy a session on a machine")
        @app_commands.describe(
            machine="Remote machine ID",
            path="Project path of the session to destroy",
        )
        async def slash_rm(interaction: discord.Interaction, machine: str, path: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_rm(channel_id, [machine, path])
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @slash_rm.autocomplete("machine")
        async def rm_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [
                mid for mid in self.config.machines
                if mid not in jump_hosts and current.lower() in mid.lower()
            ]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        @tree.command(name="mode", description="Switch Claude permission mode")
        @app_commands.describe(mode="Permission mode")
        @app_commands.choices(mode=[
            app_commands.Choice(name="bypass - Full auto (skip all permissions)", value="auto"),
            app_commands.Choice(name="code - Auto accept edits, confirm bash", value="code"),
            app_commands.Choice(name="plan - Read-only analysis", value="plan"),
            app_commands.Choice(name="ask - Confirm everything", value="ask"),
        ])
        async def slash_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_mode(channel_id, [mode.value])
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="status", description="Show current session info and queue stats")
        async def slash_status(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_status(channel_id)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="rename", description="Rename the current session")
        @app_commands.describe(name="New name for the session (e.g. my-project)")
        async def slash_rename(interaction: discord.Interaction, name: str) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_rename(channel_id, [name])
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="help", description="Show available Remote Claude commands")
        async def slash_help(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            try:
                await self.cmd_help(channel_id)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @tree.command(name="health", description="Check daemon health status")
        @app_commands.describe(machine="Remote machine ID (optional, defaults to current session or all)")
        async def slash_health(interaction: discord.Interaction, machine: Optional[str] = None) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            args = [machine] if machine else []
            try:
                await self.cmd_health(channel_id, args)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @slash_health.autocomplete("machine")
        async def health_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [
                mid for mid in self.config.machines
                if mid not in jump_hosts and current.lower() in mid.lower()
            ]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

        @tree.command(name="monitor", description="Monitor session details and queues")
        @app_commands.describe(machine="Remote machine ID (optional, defaults to current session or all)")
        async def slash_monitor(interaction: discord.Interaction, machine: Optional[str] = None) -> None:
            await interaction.response.defer()
            channel_id = self._defer_and_register(interaction)
            args = [machine] if machine else []
            try:
                await self.cmd_monitor(channel_id, args)
            except Exception as e:
                await self.send_message(channel_id, format_error(str(e)))

        @slash_monitor.autocomplete("machine")
        async def monitor_machine_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            jump_hosts = {m.proxy_jump for m in self.config.machines.values() if m.proxy_jump}
            machines = [
                mid for mid in self.config.machines
                if mid not in jump_hosts and current.lower() in mid.lower()
            ]
            return [app_commands.Choice(name=m, value=m) for m in machines][:25]

    # --- Typing Indicator ---

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
        """Stop the typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # --- Heartbeat Status Updates ---

    async def _heartbeat_loop(
        self, channel_id: str, start_time: float, event_tracker: dict
    ) -> None:
        """
        Send periodic status updates every HEARTBEAT_INTERVAL seconds
        to avoid Discord's 3-min inactivity feeling.

        event_tracker is a mutable dict shared with the streaming coroutine:
            - "last_event_type": type of last event received
            - "tool_name": current tool being used (if any)
            - "done": whether streaming is complete
            - "partial_len": length of accumulated partial content
        """
        heartbeat_count = 0
        try:
            while not event_tracker.get("done", False):
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if event_tracker.get("done", False):
                    break

                heartbeat_count += 1
                elapsed = int(time.time() - start_time)
                mins, secs = divmod(elapsed, 60)

                # Build status based on what Claude is doing
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

                # Update or create heartbeat message
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
            # Clean up heartbeat message when done
            msg = self._heartbeat_msgs.pop(channel_id, None)
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

    # --- Message Forwarding with Heartbeat ---

    async def _forward_message_with_heartbeat(self, channel_id: str, text: str, file_refs: list | None = None) -> None:
        """Forward a user message to Claude with typing indicator, heartbeat, and file upload."""
        session = self.router.resolve(channel_id)
        if not session:
            await self.send_message(
                channel_id,
                "No active session. Use `/start` to begin."
            )
            return

        # Prevent concurrent streaming
        if channel_id in self._streaming:
            await self.send_message(channel_id, "Claude is still processing. Please wait...")
            return

        self._streaming.add(channel_id)

        # Shared state between streaming and heartbeat
        event_tracker: dict = {
            "last_event_type": "",
            "tool_name": "",
            "done": False,
            "partial_len": 0,
        }

        # Start typing indicator and heartbeat
        await self._start_typing(channel_id)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(channel_id, time.time(), event_tracker)
        )

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
                event_tracker["last_event_type"] = event_type

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
                    content = event.get("content", "")
                    if content:
                        buffer += content
                        event_tracker["partial_len"] = len(buffer)
                        now = time.time()

                        if now - last_update >= STREAM_UPDATE_INTERVAL:
                            if current_msg is None:
                                current_msg = await self.send_message(channel_id, buffer + " ▌")
                            else:
                                if len(buffer) > STREAM_BUFFER_FLUSH_SIZE:
                                    await self.edit_message(channel_id, current_msg, buffer)
                                    buffer = ""
                                    event_tracker["partial_len"] = 0
                                    current_msg = None
                                else:
                                    await self.edit_message(channel_id, current_msg, buffer + " ▌")
                            last_update = now

                elif event_type == "text":
                    content = event.get("content", "")
                    if content:
                        if current_msg:
                            await self.edit_message(channel_id, current_msg, content)
                            current_msg = None
                            buffer = ""
                        else:
                            chunks = split_message(content)
                            for chunk in chunks:
                                await self.send_message(channel_id, chunk)
                        event_tracker["partial_len"] = 0

                elif event_type == "tool_use":
                    tool_name = event.get("tool", "unknown")
                    event_tracker["tool_name"] = tool_name
                    # Accumulate tool events; flush when batch is full
                    tool_batch.append(event)
                    if len(tool_batch) >= tool_batch_size:
                        batch_text = compress_tool_messages(tool_batch)
                        tool_msgs.append(batch_text)
                        await self.send_message(channel_id, batch_text)
                        tool_batch = []

                elif event_type == "result":
                    sdk_session_id = event.get("session_id")
                    if sdk_session_id:
                        self.router.update_sdk_session(channel_id, sdk_session_id)

                elif event_type == "system":
                    # System event (init, etc.) - show model info only on first connection
                    model = event.get("model")
                    if model and event.get("subtype") == "init":
                        session = self.router.resolve(channel_id)
                        daemon_sid = session.daemon_session_id if session else ""
                        if daemon_sid not in self._init_shown:
                            self._init_shown.add(daemon_sid)
                            mode_str = display_mode(session.mode) if session else "unknown"
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

    @staticmethod
    def _format_tool_use(event: dict) -> str:
        """Format a tool_use event for display."""
        tool = event.get("tool", "unknown")
        message = event.get("message", "")
        input_data = event.get("input")

        if message:
            return f"**[Tool: {tool}]** {message}"
        if input_data:
            input_str = str(input_data)
            if len(input_str) > 500:
                input_str = input_str[:497] + "..."
            return f"**[Tool: {tool}]**\n```\n{input_str}\n```"
        return f"**[Tool: {tool}]**"

    # --- Platform Methods ---

    def _get_channel_id(self, channel: discord.abc.Messageable) -> str:
        """Get our internal channel ID from a Discord channel."""
        if isinstance(channel, (discord.TextChannel, discord.DMChannel, discord.Thread)):
            return f"discord:{channel.id}"
        return f"discord:{id(channel)}"

    async def send_message(self, channel_id: str, text: str) -> Any:
        """Send a message to a Discord channel.
        
        If there's a pending deferred interaction for this channel,
        use interaction.followup.send() to complete it properly.
        """
        # Check if there's a pending deferred interaction to consume
        interaction = self._deferred_interactions.pop(channel_id, None)
        if interaction:
            try:
                chunks = split_message(text, max_len=2000)
                last_msg = None
                for i, chunk in enumerate(chunks):
                    msg = await interaction.followup.send(chunk, wait=True)
                    last_msg = msg
                return last_msg
            except discord.HTTPException as e:
                logger.warning(f"Failed to send followup, falling back to channel.send: {e}")
                # Fall through to normal send

        channel = self._channels.get(channel_id)
        if not channel:
            logger.warning(f"Channel not found: {channel_id}")
            return None

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

        return last_msg

    async def edit_message(self, channel_id: str, message_obj: Any, text: str) -> None:
        """Edit an existing Discord message."""
        if not isinstance(message_obj, discord.Message):
            return

        try:
            if len(text) > 2000:
                text = text[:1997] + "..."
            await message_obj.edit(content=text)
        except discord.HTTPException as e:
            logger.warning(f"Failed to edit message: {e}")
            try:
                channel = self._channels.get(channel_id)
                if channel:
                    await channel.send(text[:2000])
            except Exception:
                pass
        except discord.NotFound:
            pass

    async def start(self) -> None:
        """Start the Discord bot."""
        if not self.discord_config:
            raise ValueError("Discord config not set")

        token = self.discord_config.token
        if not token:
            raise ValueError("Discord token is empty. Set DISCORD_TOKEN environment variable.")

        logger.info("Starting Discord bot...")
        await self.bot.start(token)

    async def stop(self) -> None:
        """Stop the Discord bot."""
        logger.info("Stopping Discord bot...")
        # Cancel all typing tasks
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        await self.bot.close()
