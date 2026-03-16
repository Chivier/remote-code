"""
Remote Claude - Main Entry Point

Starts the Head Node with configured bots (Discord, Telegram, or both).
Uses the adapter + engine pattern: each platform gets a PlatformAdapter
paired with a BotEngine instance.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Optional

from head.config import load_config, Config
from head.ssh_manager import SSHManager
from head.session_router import SessionRouter
from head.daemon_client import DaemonClient
from head.engine import BotEngine
from head.platform.discord_adapter import DiscordAdapter
from head.platform.telegram_adapter import TelegramAdapter
from head.file_pool import FilePool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("remote-claude")

# Store startup info for restart via os.execv
_startup_executable: str = sys.executable
_startup_config_path: str = "config.yaml"
_startup_workdir: str = os.getcwd()


async def main(config_path: str = "config.yaml") -> None:
    """Main entry point."""
    # Load config
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        logger.error("Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    if not config.machines:
        logger.error("No machines configured in config.yaml")
        sys.exit(1)

    # Initialize shared components
    ssh_manager = SSHManager(config)
    session_router = SessionRouter(
        db_path=str(Path(__file__).parent / "sessions.db")
    )
    daemon_client = DaemonClient()

    # Initialize file pool for attachments
    file_pool = FilePool(
        max_size=config.file_pool.max_size,
        pool_dir=Path(config.file_pool.pool_dir).expanduser(),
        allowed_types=config.file_pool.allowed_types,
    )

    # Track adapters and engines for cleanup
    adapters: list[Any] = []
    engines: list[BotEngine] = []
    tasks: list[asyncio.Task[None]] = []

    # Initialize Discord adapter + engine
    if config.bot.discord and config.bot.discord.token:
        try:
            discord_adapter = DiscordAdapter(config, file_pool=file_pool)
            discord_engine = BotEngine(
                discord_adapter, ssh_manager, session_router,
                daemon_client, config, file_pool,
            )
            discord_adapter.set_input_handler(discord_engine.handle_input)
            discord_adapter.set_engine(discord_engine)
            adapters.append(discord_adapter)
            engines.append(discord_engine)
            logger.info("Discord bot configured")
        except Exception as e:
            logger.error(f"Failed to initialize Discord bot: {e}")

    # Initialize Telegram adapter + engine
    if config.bot.telegram and config.bot.telegram.token:
        try:
            telegram_adapter = TelegramAdapter(config.bot.telegram)
            telegram_engine = BotEngine(
                telegram_adapter, ssh_manager, session_router,
                daemon_client, config,
            )
            telegram_adapter.set_input_handler(telegram_engine.handle_input)
            adapters.append(telegram_adapter)
            engines.append(telegram_engine)
            logger.info("Telegram bot configured")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")

    if not adapters:
        logger.error("No bots configured. Set discord and/or telegram tokens in config.yaml")
        sys.exit(1)

    # Graceful shutdown handler
    shutdown_event = asyncio.Event()

    def handle_shutdown(sig: signal.Signals) -> None:
        logger.info(f"Received {sig.name}, shutting down...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown, sig)

    # Start adapters
    try:
        for adapter in adapters:
            name = adapter.platform_name
            task = asyncio.create_task(adapter.start(), name=name)
            tasks.append(task)

        logger.info(f"Remote Claude started with {len(adapters)} bot(s)")
        logger.info(f"Machines: {', '.join(config.machines.keys())}")
        logger.info(f"Default mode: {config.default_mode}")

        # Wait for shutdown signal or bot crash
        done, pending = await asyncio.wait(
            [asyncio.create_task(shutdown_event.wait()), *tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Check if any bot crashed
        for task in done:
            if task.get_name() != "None" and task.exception():
                logger.error(f"Bot {task.get_name()} crashed: {task.exception()}")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        # Cleanup
        logger.info("Cleaning up...")

        for adapter in adapters:
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning(f"Error stopping bot: {e}")

        await daemon_client.close()
        await ssh_manager.close_all()

        # Cancel remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        logger.info("Remote Claude stopped")


if __name__ == "__main__":
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    _startup_config_path = config_file
    asyncio.run(main(config_file))
