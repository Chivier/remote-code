# Getting Started

This guide walks you through setting up Remote Code from scratch.

## Prerequisites

### Local Machine (Head Node)

- **Python 3.10+**
- **pip** (Python package manager)
- **SSH keys** configured for your remote machines (or password access)
- A **Discord** bot token and/or a **Telegram** bot token

### Remote Machines

- **Node.js 18+** installed (the daemon runs on Node.js)
- **npm** (for installing daemon dependencies)
- **Claude CLI** installed and authenticated (`claude` must be in `PATH`)
- **SSH access** from your local machine

### Bot Setup

#### Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application.
3. Under "Bot", create a bot and copy the **token**.
4. Enable the **Message Content Intent** under "Privileged Gateway Intents".
5. Generate an invite URL under "OAuth2 > URL Generator" with scopes `bot` and `applications.commands`, and permissions: `Send Messages`, `Manage Messages`, `Read Message History`.
6. Invite the bot to your server.

#### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot` and follow the prompts.
3. Copy the **token** provided by BotFather.
4. Optionally, use `/setcommands` to register the bot's command list.

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Chivier/remote-code.git
cd remote-code
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `pyyaml` -- Configuration parsing
- `asyncssh` -- SSH tunnel management
- `aiohttp` -- HTTP client for daemon RPC
- `discord.py` -- Discord bot framework
- `python-telegram-bot` -- Telegram bot framework

Alternatively, install the package in editable mode to also get the `remote-code` CLI command:

```bash
pip install -e .
```

With `pip install -e .`, you can run `remote-code` directly from your terminal instead of `python -m head.main`. Both approaches install the same dependencies.

### 3. Build the Daemon (Optional)

If `daemon.auto_deploy` is `true` (the default), the daemon is built and deployed automatically on first connection. To build it manually:

```bash
cd daemon
npm install
npm run build
cd ..
```

### 4. Ensure Claude CLI on Remote Machines

On each remote machine, Claude CLI must be installed and authenticated:

```bash
# On the remote machine
npm install -g @anthropic-ai/claude-code
claude auth login
```

Verify it works:

```bash
claude --print "Hello, world" --output-format stream-json
```

## Configuration

### 1. Create `config.yaml`

```bash
cp config.example.yaml config.yaml
```

### 2. Set Environment Variables

Export your bot tokens as environment variables:

```bash
export DISCORD_TOKEN="your-discord-bot-token"
export TELEGRAM_TOKEN="your-telegram-bot-token"
```

Or hardcode them directly in `config.yaml` (not recommended for shared environments).

### 3. Configure Machines

Edit `config.yaml` to add your remote machines:

```yaml
machines:
  gpu-1:
    host: gpu1.example.com
    user: your-user
    daemon_port: 9100
    default_paths:
      - /home/your-user/project-a
      - /home/your-user/project-b
```

See the [Configuration Guide](./configuration.md) for all available options.

## Running

Start the Head Node:

```bash
python -m head.main
```

Or with a custom config file:

```bash
python -m head.main /path/to/config.yaml
```

You should see output like:

```
2026-03-14 10:00:00 [remote-code] INFO: Discord bot configured
2026-03-14 10:00:00 [remote-code] INFO: Telegram bot configured
2026-03-14 10:00:00 [remote-code] INFO: Remote Code started with 2 bot(s)
2026-03-14 10:00:00 [remote-code] INFO: Machines: gpu-1
2026-03-14 10:00:00 [remote-code] INFO: Default mode: auto
```

## First Session

1. Open Discord or Telegram.
2. In an allowed channel, use the `/start` command:

   ```
   /start gpu-1 /home/your-user/project-a
   ```

3. The system will:
   - Establish an SSH tunnel to `gpu-1`
   - Deploy the daemon if needed (auto-deploy)
   - Start the daemon process on the remote machine
   - Sync skills files if configured
   - Create a Claude session on the remote machine
4. Send a message to interact with Claude:

   ```
   What files are in this project?
   ```

5. Claude's response streams back in real-time.

## Stopping

Press `Ctrl+C` or send `SIGTERM` to the process. The Head Node will:

1. Stop all bots gracefully
2. Close the daemon client HTTP session
3. Close all SSH tunnels
4. Cancel pending tasks

Sessions on remote daemons are **not** destroyed on Head Node shutdown -- they can be resumed later with `/resume`.
