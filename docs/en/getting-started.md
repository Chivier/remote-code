# Getting Started

This guide walks you through setting up Codecast from scratch. By the end you will have a bot running on Discord, Telegram, or Lark that lets you interact with AI CLI tools on a remote machine.

## Prerequisites

### Local Machine (Head Node)

- Python 3.10 or later
- pip (Python package manager)
- SSH keys configured for your remote machines (or password access)
- A bot token for at least one of: Discord, Telegram, or Lark

### Remote Machines

- SSH access from your local machine
- At least one AI CLI installed and authenticated:
  - Claude CLI (`claude` in PATH)
  - Codex (`codex` in PATH)
  - Gemini CLI (`gemini` in PATH)
  - OpenCode (`opencode` in PATH)

No Node.js or npm is required on remote machines. The Codecast daemon is a single static Rust binary that is deployed automatically via SCP.

## Bot Setup

### Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application, then under "Bot" create a bot and copy the token.
3. Enable the "Message Content Intent" under "Privileged Gateway Intents".
4. Under "OAuth2 > URL Generator", select scopes `bot` and `applications.commands`, with permissions: Send Messages, Manage Messages, Read Message History.
5. Use the generated URL to invite the bot to your server.

### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot` and follow the prompts.
3. Copy the token BotFather provides.

### Lark (Feishu)

1. Go to the [Lark Open Platform](https://open.feishu.cn/app) and create an application.
2. Under "Permissions & Scopes", add: `im:message`, `im:message:send_as_bot`.
3. Under "Event Subscriptions", add the `im.message.receive_v1` event.
4. Copy the App ID and App Secret.
5. Set up a webhook endpoint or use Lark's built-in bot messaging.

## Installation

### Option 1: Install from PyPI (recommended)

```bash
pip install codecast
```

This installs all dependencies and provides the `codecast` command.

### Option 2: Install from source

```bash
git clone https://github.com/Chivier/codecast.git
cd codecast
pip install -e .
```

The `pip install -e .` command installs the package in editable mode and provides the `codecast` command.

### Building the Daemon Manually (optional)

If `daemon.auto_deploy` is enabled (the default), the daemon binary is deployed to remote machines automatically on first connection. If you need to build it manually:

```bash
cargo build --release
```

The output binary is `target/release/codecast-daemon`. Copy it to `~/.codecast/daemon/codecast-daemon` on your local machine and it will be deployed from there.

### Installing the AI CLI on Remote Machines

Each remote machine needs at least one AI CLI installed. For Claude CLI:

```bash
# On the remote machine
npm install -g @anthropic-ai/claude-code
claude auth login
```

Verify the CLI works:

```bash
claude --print "Hello" --output-format stream-json
```

## Configuration

### 1. Create the config file

The primary config location is `~/.codecast/config.yaml`. Create the directory and copy the example:

```bash
mkdir -p ~/.codecast
cp /path/to/codecast/config.example.yaml ~/.codecast/config.yaml
```

Alternatively, you can place `config.yaml` in the current working directory as a development fallback.

### 2. Set environment variables

Export your bot tokens:

```bash
export DISCORD_TOKEN="your-discord-bot-token"
export TELEGRAM_TOKEN="your-telegram-bot-token"
export LARK_APP_ID="your-lark-app-id"
export LARK_APP_SECRET="your-lark-app-secret"
```

Config values support `${ENV_VAR}` syntax, so tokens are never hardcoded in the file.

### 3. Configure your remote machines

Edit `~/.codecast/config.yaml` to add your machines under `peers:`:

```yaml
peers:
  gpu-1:
    host: gpu1.example.com
    user: your-user
    daemon_port: 9100
    default_paths:
      - /home/your-user/project-a
      - /home/your-user/project-b

bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 1234567890123456789

default_mode: auto
```

See the [Configuration Guide](./configuration.md) for all available options, including Telegram, Lark, ProxyJump, and file forwarding.

## First-Time Setup with the TUI Wizard

If this is your first time using Codecast, the TUI (terminal UI) provides an interactive setup wizard:

```bash
codecast tui
```

The TUI lets you configure machines, test SSH connections, and start sessions without editing YAML directly.

## Running Codecast

Start the head node:

```bash
codecast
```

With a specific config file:

```bash
codecast /path/to/config.yaml
```

You should see output like:

```
INFO: Discord bot configured
INFO: Telegram bot configured
INFO: Codecast started with 2 bot(s)
INFO: Peers: gpu-1
INFO: Default mode: auto
```

## Starting Your First Session

1. Open Discord, Telegram, or Lark.
2. In an allowed channel or chat, use the `/start` command:

   ```
   /start gpu-1 /home/your-user/project-a
   ```

3. Codecast will:
   - Establish an SSH tunnel to `gpu-1`
   - Deploy the daemon binary if not already present (auto-deploy)
   - Start the daemon process on the remote machine
   - Sync skills files if configured
   - Create an AI session in the project directory

4. Send a message to interact:

   ```
   What files are in this project?
   ```

5. The response streams back in real-time.

To start a session with a specific CLI other than Claude:

```
/start gpu-1 /home/your-user/project --cli codex
```

Supported CLI types: `claude`, `codex`, `gemini`, `opencode`.

## Stopping Codecast

Press `Ctrl+C` or send `SIGTERM` to the process. The head node will:

1. Stop all bots gracefully
2. Close the daemon client HTTP session
3. Close all SSH tunnels
4. Cancel pending tasks

Sessions on remote daemons are not destroyed on head node shutdown. Resume them later with `/resume`.

## Troubleshooting

**"Could not connect to machine"** -- Check that the SSH host, user, and key are correct. Test with `ssh user@host` from your terminal. If the machine is behind a jump host, see the `proxy_jump` option in the [Configuration Guide](./configuration.md).

**"Daemon not found after deploy"** -- Check that `~/.codecast/daemon/` exists on the remote machine and the binary is executable. Check `~/.codecast/daemon.log` on the remote machine for errors.

**"claude: command not found"** -- Claude CLI is not installed or not in PATH on the remote machine. The daemon inherits the SSH session's PATH; ensure the CLI is accessible via a normal SSH login.

**Bot does not respond** -- Confirm the bot token is valid and the channel or user ID is in the allowed list. For Discord, check that the Message Content Intent is enabled.

**Session appears stuck** -- Use `/interrupt` or `/stop` in the chat to interrupt the current AI operation. Use `/status` to check queue state.
