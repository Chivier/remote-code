# Remote Code

A bot-based system that lets you interact with [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) on remote machines through Discord and Telegram.

## How It Works

```
  Discord / Telegram
        │
        ▼
  Head Node (Python, runs locally)
        │ SSH tunnel
        ▼
  Daemon (Node.js, runs on each remote machine)
        │ stdin/stdout JSON-lines
        ▼
  Claude CLI (long-lived subprocess)
```

The **Head Node** runs on your local machine as a chat bot. When you send a message, it's forwarded over SSH to a **Daemon** on the remote machine, which passes it to a persistent Claude CLI process. Responses stream back in real time.

Key properties:
- Claude CLI processes are **long-lived** — context persists across messages without reloading
- All communication goes through an **SSH tunnel** — the daemon is not exposed to the internet
- Sessions can be **detached and resumed** — leave a task running, reconnect later
- **Multiple machines** — connect to as many remote servers as you want

## Quick Start

```bash
git clone https://github.com/your-org/remote-code.git
cd remote-code
pip install -r requirements.txt
cd daemon && npm install && npm run build && cd ..
cp config.example.yaml config.yaml
# Edit config.yaml, then:
export DISCORD_TOKEN="your-bot-token"
python -m head.main
```

See [Getting Started](./docs/getting-started.md) for the full walkthrough.

## Documentation

- [Getting Started](./docs/getting-started.md) — installation, prerequisites, first session
- [Adding a Discord Bot](./docs/adding-a-discord-bot.md) — create a Discord Application step by step
- [Adding a Server](./docs/adding-a-server.md) — direct SSH, jump hosts, password auth, custom Node.js path
- [Commands Reference](./docs/commands-reference.md) — all bot commands with examples

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start <machine> <path>` | Start a new Claude session |
| `/resume <session_id>` | Resume a detached session |
| `/ls machine` | List configured machines |
| `/ls session` | List active/detached sessions |
| `/exit` | Detach (keeps process running) |
| `/rm <machine> <path>` | Destroy a session |
| `/mode <auto\|code\|plan\|ask>` | Switch permission mode |
| `/status` | Show current session info |
| `/health [machine]` | Daemon health check |
| `/monitor [machine]` | Session and queue details |

## Permission Modes

| Mode | Behavior |
|------|----------|
| `auto` | Full autonomous — skips all permission prompts |
| `code` | Auto-accepts file edits; confirms bash commands |
| `plan` | Read-only analysis; no file writes |
| `ask` | Confirms every action |

## Requirements

- Python 3.11+ (Head Node)
- Node.js 18+ and npm (daemon build)
- SSH access to remote machine(s)
- Claude CLI installed and authenticated on remote machines
- Discord bot token and/or Telegram bot token
