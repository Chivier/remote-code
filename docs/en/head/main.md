# Entry Point (main.py)

**File:** `head/main.py`

The entry point for the Remote Code Head Node. This module bootstraps the entire system by loading configuration, initializing shared components, starting bots, and handling graceful shutdown.

## Purpose

- Load and validate `config.yaml`
- Create shared infrastructure (SSHManager, SessionRouter, DaemonClient)
- Initialize and start Discord and/or Telegram bots
- Handle graceful shutdown on SIGTERM/SIGINT

## Main Function

```python
async def main(config_path: str = "config.yaml") -> None
```

The `main()` coroutine is the primary entry point. It performs the following steps:

### 1. Configuration Loading

```python
config = load_config(config_path)
```

Loads `config.yaml` (or a custom path passed as a command-line argument). If the file is missing or invalid, the process exits with an error message.

### 2. Shared Component Initialization

```python
ssh_manager = SSHManager(config)
session_router = SessionRouter(db_path=str(Path(__file__).parent / "sessions.db"))
daemon_client = DaemonClient()
```

These three components are created once and shared across all bots:

- **SSHManager**: Manages SSH connections and tunnels to all configured machines. Takes the full config to access machine definitions and daemon deployment settings.
- **SessionRouter**: SQLite-backed session registry. The database is stored as `head/sessions.db` (next to the Python source files).
- **DaemonClient**: Stateless JSON-RPC client with a shared aiohttp session.

### 3. Bot Initialization

```python
discord_bot = DiscordBot(ssh_manager, session_router, daemon_client, config)
telegram_bot = TelegramBot(ssh_manager, session_router, daemon_client, config)
```

Each bot is created only if its token is configured. If neither bot has a valid token, the process exits with an error.

### 4. Bot Startup

```python
task = asyncio.create_task(discord_bot.start(), name="discord")
task = asyncio.create_task(telegram_bot.start(), name="telegram")
```

Bots run as concurrent asyncio tasks. The main coroutine then waits for either:
- A shutdown signal (SIGTERM/SIGINT)
- A bot task to crash (first completed)

### 5. Graceful Shutdown

```python
def handle_shutdown(sig: signal.Signals) -> None:
    shutdown_event.set()
```

Signal handlers for `SIGTERM` and `SIGINT` set a shutdown event. When triggered:

1. All bots are stopped via `bot.stop()`
2. The DaemonClient's HTTP session is closed
3. All SSH tunnels are closed via `ssh_manager.close_all()`
4. Remaining asyncio tasks are cancelled

## Command-Line Usage

```bash
# Default config
python -m head.main

# Custom config path
python -m head.main /path/to/config.yaml
```

The config path is read from `sys.argv[1]` if provided, defaulting to `"config.yaml"`.

## Logging

The module configures Python's logging at the `INFO` level with the format:

```
2026-03-14 10:00:00 [remote-code] INFO: message
```

All modules under `head/` use `logging.getLogger(__name__)` and inherit this configuration.

## Error Handling

- Missing config file: logs error and exits with code 1
- Empty machines dict: logs error and exits with code 1
- No bots configured (no tokens): logs error and exits with code 1
- Bot crash during runtime: logs the exception, triggers shutdown
- Cleanup errors: logged as warnings, do not prevent other cleanup steps
