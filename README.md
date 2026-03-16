# Remote Code

A bot-based system that lets you interact with [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) on remote machines through Discord and Telegram.

## How It Works

```
  Discord / Telegram
        |
        v
  Head Node (Python, runs locally)
        | SSH tunnel
        v
  Daemon (Rust, runs on each remote machine)
        | stdin/stdout JSON-lines
        v
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
git clone https://github.com/Chivier/remote-claude.git
cd remote-claude
pip install -e .              # installs deps + "remote-code" CLI command
cp config.example.yaml config.yaml
# Edit config.yaml with your machines and bot token, then:
python -m head.main
```

Or install dependencies only:

```bash
pip install -r requirements.txt
```

See [Getting Started](./docs/getting-started.md) for the full walkthrough.

## Documentation

- [Getting Started](./docs/getting-started.md) — installation, prerequisites, first session
- [Adding a Discord Bot](./docs/adding-a-discord-bot.md) — create a Discord Application step by step
- [Adding a Telegram Bot](./docs/adding-a-telegram-bot.md) — create a Telegram bot via BotFather
- [Adding a Server](./docs/adding-a-server.md) — direct SSH, jump hosts, password auth
- [Commands Reference](./docs/commands-reference.md) — all bot commands with examples

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start <machine> <path>` | Start a new Claude session |
| `/resume <name_or_id>` | Resume a detached session |
| `/new` | New session in same directory (detaches current) |
| `/clear` | Clear context: destroy + restart in same directory |
| `/ls machine` | List configured machines |
| `/ls session` | List active/detached sessions |
| `/exit` | Detach (keeps process running) |
| `/rm <machine> <path>` | Destroy a session |
| `/mode <auto\|code\|plan\|ask>` | Switch permission mode |
| `/rename <new_name>` | Rename current session |
| `/interrupt` | Interrupt Claude's current operation |
| `/status` | Show current session info |
| `/health [machine]` | Daemon health check |
| `/monitor [machine]` | Session and queue details |
| `/add-machine <name>` | Add machine from SSH config |
| `/remove-machine <name>` | Remove a machine |
| `/update` | Git pull + restart (admin) |
| `/restart` | Restart head node (admin) |

## Permission Modes

| Mode | Behavior |
|------|----------|
| `auto` | Full autonomous — skips all permission prompts |
| `code` | Auto-accepts file edits; confirms bash commands |
| `plan` | Read-only analysis; no file writes |
| `ask` | Confirms every action |

## Configuration

Config files are searched in this order:
1. CLI argument: `python -m head.main /path/to/config.yaml`
2. `~/.remote-code/config.yaml`
3. `./config.yaml` (development fallback)

Auto-migration: if `~/.remote-claude/` exists and `~/.remote-code/` does not, it is automatically moved on startup.

## Requirements

- Python 3.11+ (Head Node)
- SSH access to remote machine(s)
- Claude CLI installed and authenticated on remote machines
- Discord bot token and/or Telegram bot token
