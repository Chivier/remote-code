# Getting Started

This guide walks you through deploying Remote Claude from scratch: setting up the Head Node on your local machine, configuring a Discord or Telegram bot, and connecting your first remote server.

## Prerequisites

Before you begin, make sure you have:

- **Local machine** (where you run the Head Node)
  - Python 3.11+
  - Node.js 18+ and npm (for building the daemon)
  - SSH access to your remote machine(s)
- **Remote machine(s)**
  - SSH server running
  - Node.js 18+ (for the daemon)
  - [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- **A bot token** — Discord or Telegram (or both)

---

## Step 1: Clone and Install

```bash
git clone https://github.com/your-org/remote-claude.git
cd remote-claude

# Install Python dependencies
pip install -r requirements.txt

# Build the daemon (TypeScript → JavaScript)
cd daemon && npm install && npm run build && cd ..
```

---

## Step 2: Create Your Config File

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` in your editor. The file has four main sections:

| Section | Purpose |
|---------|---------|
| `machines` | Remote servers Claude will run on |
| `bot` | Discord and/or Telegram bot credentials |
| `default_mode` | Permission mode for new sessions |
| `daemon` | Where/how to deploy the daemon on remotes |

A minimal working config looks like this:

```yaml
machines:
  my-server:
    host: 192.168.1.100
    user: alice
    daemon_port: 9100
    default_paths:
      - /home/alice/myproject

bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 1234567890123456789  # your channel ID

default_mode: auto
```

See [Adding a Discord Bot](./adding-a-discord-bot.md) and [Adding a Server](./adding-a-server.md) for detailed setup of each section.

---

## Step 3: Set Environment Variables

The config uses `${ENV_VAR}` substitution. Export your tokens before running:

```bash
export DISCORD_TOKEN="your-discord-bot-token-here"
# and/or
export TELEGRAM_TOKEN="your-telegram-bot-token-here"
```

For persistence, add these to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.) or use a `.env` file with a tool like `direnv`.

---

## Step 4: Verify SSH Access

Make sure you can SSH into your remote machine without a passphrase prompt:

```bash
ssh alice@192.168.1.100 "echo 'SSH OK'"
```

If this requires a password every time, set up SSH key authentication:

```bash
# Generate a key if you don't have one
ssh-keygen -t ed25519 -C "remote-claude"

# Copy the public key to the remote machine
ssh-copy-id alice@192.168.1.100
```

---

## Step 5: Verify Claude CLI on the Remote

SSH into your remote machine and confirm Claude CLI is installed and authenticated:

```bash
ssh alice@192.168.1.100
claude --version   # should print the version
claude            # should open the interactive prompt
```

If `claude` is not in the default PATH (e.g. installed via pip in a user environment), note its path — you may need to set `node_path` in the config. See [Adding a Server](./adding-a-server.md#custom-node-path).

---

## Step 6: Run the Head Node

```bash
python -m head.main
```

You should see output like:

```
2026-03-14 10:00:00 [remote-claude] INFO: Discord bot configured
2026-03-14 10:00:01 [remote-claude] INFO: Remote Claude started with 1 bot(s)
2026-03-14 10:00:01 [remote-claude] INFO: Machines: my-server
2026-03-14 10:00:01 [remote-claude] INFO: Default mode: auto
2026-03-14 10:00:02 [discord] INFO: Discord bot logged in as RemoteClaude#1234
2026-03-14 10:00:02 [discord] INFO: Synced 9 slash command(s)
```

---

## Step 7: Start Your First Session

In your Discord channel (or Telegram chat), use the `/start` command:

```
/start my-server /home/alice/myproject
```

The bot will:
1. Open an SSH tunnel to `my-server`
2. Deploy the daemon if not already present (`auto_deploy: true`)
3. Spawn a Claude CLI process in `/home/alice/myproject`
4. Reply with the model name and permission mode

Once connected, just type messages — no command prefix needed. The bot forwards everything to Claude.

---

## What Happens on First Connect

When `auto_deploy: true` (the default), the Head Node automatically:

1. Builds the daemon locally (`daemon/dist/`)
2. SCPs it to the remote machine (`~/.remote-claude/daemon/`)
3. Runs `npm install --production` on the remote
4. Starts the daemon with `nohup node dist/server.js`
5. Waits up to 30 seconds for it to become healthy

This means the remote machine needs npm available. After the first deploy, the daemon stays running across SSH reconnections.

---

## Running as a Service (Optional)

For production use, run the Head Node as a systemd service so it restarts automatically:

```ini
# /etc/systemd/system/remote-claude.service
[Unit]
Description=Remote Claude Head Node
After=network.target

[Service]
Type=simple
User=alice
WorkingDirectory=/home/alice/remote-claude
Environment="DISCORD_TOKEN=your-token-here"
ExecStart=/home/alice/remote-claude/.venv/bin/python -m head.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable remote-claude
sudo systemctl start remote-claude
sudo journalctl -u remote-claude -f
```

---

## Next Steps

- [Adding a Discord Bot](./adding-a-discord-bot.md) — create and configure the Discord application
- [Adding a Server](./adding-a-server.md) — connect more remote machines, including jump-host setups
- [Commands Reference](./commands-reference.md) — full list of bot commands
