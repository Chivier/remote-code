# Configuration Guide

Remote Code is configured via a `config.yaml` file in the project root. Copy `config.example.yaml` to get started:

```bash
cp config.example.yaml config.yaml
```

## Environment Variable Expansion

All string values in `config.yaml` support `${ENV_VAR}` syntax for environment variable substitution. If the variable is not set, the `${...}` expression is left as-is.

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}  # Replaced with the value of $DISCORD_TOKEN
```

Path values also support `~` expansion (e.g., `~/.ssh/id_rsa` becomes `/home/user/.ssh/id_rsa`).

## Full Configuration Reference

### `machines`

Defines the remote machines that Remote Code can connect to. Each key is a machine ID used in commands (e.g., `/start gpu-1 /path`).

```yaml
machines:
  gpu-1:
    host: gpu1.example.com
    user: your-ssh-user
    ssh_key: ~/.ssh/id_rsa
    port: 22
    proxy_jump: gateway
    proxy_command: "ssh -W %h:%p gateway"
    password: file:~/.ssh/gpu1-password
    daemon_port: 9100
    node_path: /usr/local/bin/node
    default_paths:
      - /home/your-user/project-a
      - /home/your-user/project-b
```

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | *(machine ID)* | Hostname or IP address of the remote machine. Defaults to the machine ID key if omitted. |
| `user` | string | `$USER` | SSH username. Defaults to the current user's login name. |
| `ssh_key` | string | *(none)* | Path to SSH private key. If omitted, uses the ssh-agent or default keys. Supports `~` expansion. |
| `port` | int | `22` | SSH port. |
| `proxy_jump` | string | *(none)* | Machine ID of a jump host (must also be defined in `machines`). Uses SSH tunneling through the jump host to reach this machine. |
| `proxy_command` | string | *(none)* | SSH `ProxyCommand` string for advanced proxy configurations. |
| `password` | string | *(none)* | SSH password. Supports `file:/path/to/file` syntax to read the password from a file (useful for automation). The file content is trimmed of whitespace. |
| `daemon_port` | int | `9100` | Port the daemon listens on (on the remote machine, bound to `127.0.0.1`). The Head Node creates an SSH tunnel to this port. |
| `node_path` | string | `"node"` | Full path to the Node.js binary on the remote machine. Useful when Node.js is installed in a non-standard location. The parent directory is also added to `PATH` so that `npm` and `claude` CLI can be found. |
| `default_paths` | list[string] | `[]` | Commonly used project paths on this machine. Used for autocomplete in Discord slash commands and displayed in `/ls machine` output. |

#### ProxyJump Example

For machines behind a bastion/jump host:

```yaml
machines:
  gateway:
    host: bastion.example.com
    user: admin
    port: 22

  gpu-2:
    host: gpu2.lab.internal
    user: researcher
    proxy_jump: gateway     # Connect through 'gateway' first
    daemon_port: 9100
    default_paths:
      - /data/experiments
```

The `gateway` machine is automatically skipped in `/ls machine` output if it has no `default_paths` and is only used as a jump host.

#### Password from File

```yaml
machines:
  secure-node:
    host: 10.0.1.50
    user: deploy
    password: file:~/.secrets/secure-node-password
```

The `file:` prefix reads the password from the specified file path. The file should contain only the password (whitespace is trimmed).

### `bot`

Configures the Discord and/or Telegram bot connections. At least one bot must be configured with a valid token for Remote Code to start.

#### `bot.discord`

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 123456789012345678
      - 987654321098765432
    command_prefix: "/"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `token` | string | *(required)* | Discord bot token. Use `${DISCORD_TOKEN}` to read from environment. |
| `allowed_channels` | list[int] | `[]` | List of Discord channel IDs where the bot will respond. Empty list means all channels. |
| `command_prefix` | string | `"/"` | Command prefix for text-based commands. Slash commands always use `/` regardless of this setting. |

#### `bot.telegram`

```yaml
bot:
  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users:
      - 123456789
      - 987654321
```

| Field | Type | Default | Description |
|---|---|---|---|
| `token` | string | *(required)* | Telegram bot token from @BotFather. Use `${TELEGRAM_TOKEN}` to read from environment. |
| `allowed_users` | list[int] | `[]` | List of Telegram user IDs allowed to interact with the bot. Empty list means all users. |

### `default_mode`

```yaml
default_mode: auto
```

The default permission mode for new sessions. One of:

| Mode | CLI Flag | Description |
|---|---|---|
| `auto` | `--dangerously-skip-permissions` | Full automation -- Claude can read, write, and execute anything without confirmation. Displayed as "bypass" in bot output. |
| `code` | *(none)* | Auto-accept file edits, but prompt for bash commands. |
| `plan` | *(none)* | Read-only analysis mode. Claude cannot make changes. |
| `ask` | *(none)* | Confirm everything. Every tool use requires approval. |

The mode can be changed at any time during a session with the `/mode` command.

### `skills`

```yaml
skills:
  shared_dir: ./skills
  sync_on_start: true
```

| Field | Type | Default | Description |
|---|---|---|---|
| `shared_dir` | string | `"./skills"` | Local directory containing shared skills to sync to remote machines. Should contain `CLAUDE.md` and/or `.claude/skills/` subdirectory. |
| `sync_on_start` | bool | `true` | Whether to sync skills when creating a new session with `/start`. Skills are copied to the project directory on the remote machine, but existing files are never overwritten. |

### `daemon`

```yaml
daemon:
  install_dir: ~/.remote-code/daemon
  auto_deploy: true
  log_file: ~/.remote-code/daemon.log
```

| Field | Type | Default | Description |
|---|---|---|---|
| `install_dir` | string | `"~/.remote-code/daemon"` | Directory on the remote machine where the daemon code is installed. Contains `dist/`, `node_modules/`, and `package.json`. |
| `auto_deploy` | bool | `true` | Automatically build the daemon locally and deploy via SCP if it is not already installed on the remote machine. If `false`, you must install the daemon manually. |
| `log_file` | string | `"~/.remote-code/daemon.log"` | Path to the daemon's log file on the remote machine. Daemon stdout/stderr is redirected here when started via `nohup`. |

## Complete Example

```yaml
machines:
  gateway:
    host: bastion.university.edu
    user: admin

  gpu-1:
    host: gpu-node-1.internal
    user: researcher
    proxy_jump: gateway
    daemon_port: 9100
    node_path: /opt/node/bin/node
    default_paths:
      - /data/ml-project
      - /data/nlp-experiments

  cloud-dev:
    host: 203.0.113.42
    user: ubuntu
    ssh_key: ~/.ssh/cloud-key.pem
    daemon_port: 9100
    default_paths:
      - /home/ubuntu/webapp

bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 1234567890123456789

  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users:
      - 987654321

default_mode: auto

skills:
  shared_dir: ./skills
  sync_on_start: true

daemon:
  install_dir: ~/.remote-code/daemon
  auto_deploy: true
  log_file: ~/.remote-code/daemon.log
```
