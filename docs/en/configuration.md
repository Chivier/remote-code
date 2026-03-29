# Configuration Guide

Codecast reads its configuration from a YAML file. The file is searched in this order:

1. A path provided as a CLI argument: `codecast /path/to/config.yaml`
2. `~/.codecast/config.yaml` (recommended location)
3. `./config.yaml` in the current working directory (development fallback)

To get started, copy the example config:

```bash
mkdir -p ~/.codecast
cp /path/to/codecast/config.example.yaml ~/.codecast/config.yaml
```

## Environment Variable Expansion

All string values support `${ENV_VAR}` syntax. If the variable is not set, the expression is left as-is.

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}
```

Path values also support `~` expansion (e.g. `~/.ssh/id_rsa` expands to `/home/user/.ssh/id_rsa`).

Passwords can be read from a file using the `file:` prefix:

```yaml
password: file:~/.secrets/my-password
```

The file should contain only the password text (whitespace is trimmed).

---

## `peers`

Defines the remote machines (peers) that Codecast can connect to. Each key is a machine ID used in commands like `/start gpu-1 /path`.

```yaml
peers:
  gpu-1:
    host: gpu1.example.com
    user: your-ssh-user
    ssh_key: ~/.ssh/id_rsa
    port: 22
    proxy_jump: gateway
    password: file:~/.secrets/gpu1-password
    daemon_port: 9100
    default_paths:
      - /home/your-user/project-a
      - /home/your-user/project-b
```

### Peer transport types

| `transport` value | Description |
|---|---|
| `ssh` | Connect via SSH tunnel (default) |
| `http` | Connect directly over HTTP/HTTPS (no SSH) |
| `local` | Local machine; SSH tunnel is skipped |

For `ssh` transport (the default), use these fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | *(machine ID)* | Hostname or IP of the remote machine. Defaults to the machine ID if omitted. |
| `user` | string | `$USER` | SSH username. |
| `ssh_key` | string | *(none)* | Path to SSH private key. If omitted, uses the ssh-agent or default keys. |
| `port` | int | `22` | SSH port. |
| `proxy_jump` | string | *(none)* | Machine ID of a jump host (must also be defined under `peers`). |
| `proxy_command` | string | *(none)* | SSH ProxyCommand string for advanced proxy configurations. |
| `password` | string | *(none)* | SSH password, or `file:/path/to/file` to read from a file. |
| `daemon_port` | int | `9100` | Port the daemon listens on, bound to `127.0.0.1` on the remote machine. |
| `default_paths` | list[string] | `[]` | Commonly used project paths. Used for autocomplete in Discord and displayed in `/ls machine`. |

For `http` transport, use these fields:

| Field | Type | Description |
|---|---|---|
| `address` | string | Full URL of the daemon (e.g. `https://myserver.example.com:9100`) |
| `token` | string | Authentication token |
| `tls_fingerprint` | string | Optional TLS certificate fingerprint for pinning |

### ProxyJump example

For machines behind a bastion host:

```yaml
peers:
  gateway:
    host: bastion.example.com
    user: admin

  gpu-2:
    host: gpu2.lab.internal
    user: researcher
    proxy_jump: gateway
    daemon_port: 9100
    default_paths:
      - /data/experiments
```

The `gateway` peer is used only as a jump host and does not need `default_paths`.

---

## `bot`

Configures the bot connections. At least one bot must be configured for Codecast to start.

### `bot.discord`

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 123456789012345678
    admin_users:
      - 987654321098765432
    command_prefix: "/"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `token` | string | *(required)* | Discord bot token. |
| `allowed_channels` | list[int] | `[]` | Channel IDs where the bot responds. Empty means all channels. |
| `admin_users` | list[int] | `[]` | Discord user IDs allowed to use `/update` and `/restart`. |
| `command_prefix` | string | `"/"` | Prefix for text-based commands. Slash commands always use `/`. |

### `bot.telegram`

```yaml
bot:
  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users:
      - 123456789
    allowed_chats:
      - -1001234567890
    admin_users:
      - 123456789
```

| Field | Type | Default | Description |
|---|---|---|---|
| `token` | string | *(required)* | Telegram bot token from @BotFather. |
| `allowed_users` | list[int] | `[]` | Telegram user IDs allowed to use the bot. Empty means all users. |
| `allowed_chats` | list[int] | `[]` | Chat IDs (groups or channels) allowed. Empty means all chats. |
| `admin_users` | list[int] | `[]` | User IDs allowed to use `/update` and `/restart`. |

### `bot.lark`

```yaml
bot:
  lark:
    app_id: ${LARK_APP_ID}
    app_secret: ${LARK_APP_SECRET}
    allowed_chats:
      - "oc_abcdef1234567890"
    admin_users:
      - "ou_abcdef1234567890"
    use_cards: true
```

| Field | Type | Default | Description |
|---|---|---|---|
| `app_id` | string | *(required)* | Lark application App ID. |
| `app_secret` | string | *(required)* | Lark application App Secret. |
| `allowed_chats` | list[string] | `[]` | Lark chat IDs allowed. Empty means all chats. |
| `admin_users` | list[string] | `[]` | Lark user open IDs allowed to use `/update` and `/restart`. |
| `use_cards` | bool | `true` | Use interactive cards for questions and tool displays. |

### `bot.webui`

Enables the browser-based Web UI alongside the chat bots.

```yaml
bot:
  webui:
    enabled: true
    port: 8080
    host: 127.0.0.1
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable the Web UI. |
| `port` | int | `8080` | Port to listen on. |
| `host` | string | `"127.0.0.1"` | Address to bind to. Use `0.0.0.0` to allow external access. |

---

## `default_mode`

```yaml
default_mode: auto
```

The default permission mode for new sessions. Can be changed per-session with `/mode`.

| Mode | Description |
|---|---|
| `auto` | Full automation. The AI can read, write, and execute anything without confirmation. Displayed as "bypass" in bot output. |
| `code` | Auto-accept file edits, prompt for shell commands. |
| `plan` | Read-only analysis. The AI cannot make changes. |
| `ask` | Confirm every tool use. |

---

## `tool_batch_size`

```yaml
tool_batch_size: 15
```

The number of consecutive tool_use events (file reads, shell commands, etc.) that are compressed into a single summary message. Reduces chat noise during large operations. Default is `15`.

---

## `skills`

```yaml
skills:
  shared_dir: ./skills
  sync_on_start: true
```

| Field | Type | Default | Description |
|---|---|---|---|
| `shared_dir` | string | `"./skills"` | Local directory containing shared skills to sync. Should contain `CLAUDE.md` and/or `.claude/skills/`. |
| `sync_on_start` | bool | `true` | Sync skills when creating a new session with `/start`. Existing files on the remote are never overwritten. |

---

## `daemon`

Controls how the Codecast daemon (a single static Rust binary) is deployed to and managed on remote machines.

```yaml
daemon:
  install_dir: ~/.codecast/daemon
  auto_deploy: true
  log_file: ~/.codecast/daemon.log
```

| Field | Type | Default | Description |
|---|---|---|---|
| `install_dir` | string | `"~/.codecast/daemon"` | Directory on the remote machine where the daemon binary is installed. |
| `auto_deploy` | bool | `true` | Automatically deploy the daemon via SCP if not already present or if the version does not match. |
| `log_file` | string | `"~/.codecast/daemon.log"` | Path to the daemon log file on the remote machine. |

No Node.js or npm is required on remote machines. The daemon is a self-contained binary with no external runtime dependencies.

---

## `file_pool`

Controls how files uploaded to bot chats are staged for use in AI sessions.

```yaml
file_pool:
  max_size: 1073741824
  pool_dir: ~/.codecast/file-pool
  remote_dir: /tmp/codecast/files
  allowed_types:
    - text/plain
    - text/markdown
    - application/pdf
    - image/*
    - video/*
    - audio/*
```

| Field | Type | Default | Description |
|---|---|---|---|
| `max_size` | int | `1073741824` (1 GB) | Maximum total size of the local file pool in bytes. |
| `pool_dir` | string | `"~/.codecast/file-pool"` | Local directory where uploaded files are cached. |
| `remote_dir` | string | `"/tmp/codecast/files"` | Directory on the remote machine where files are uploaded before being passed to the AI. |
| `allowed_types` | list[string] | *(see above)* | MIME type patterns for accepted files. Wildcards are supported (e.g. `image/*`). |

---

## `file_forward`

Controls automatic forwarding of files from remote machines to chat when the AI's response contains matching file paths.

```yaml
file_forward:
  enabled: true
  download_dir: ~/.codecast/downloads
  default_max_size: 5242880
  default_auto: false
  rules:
    - pattern: "*.png"
      max_size: 10485760
      auto: true
    - pattern: "*.log"
      max_size: 1048576
      auto: false
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable file forwarding. |
| `download_dir` | string | `"~/.codecast/downloads"` | Local directory where downloaded files are temporarily stored. |
| `default_max_size` | int | `5242880` (5 MB) | Default maximum file size for forwarding, in bytes. |
| `default_auto` | bool | `false` | Automatically forward matched files without prompting. |
| `rules` | list | `[]` | Per-pattern overrides. |

Each rule in `rules` has:

| Field | Type | Description |
|---|---|---|
| `pattern` | string | Glob pattern matched against the file path (e.g. `*.png`, `*.log`). |
| `max_size` | int | Maximum file size for this rule, in bytes. |
| `auto` | bool | If `true`, the file is sent automatically. If `false`, a prompt is shown. |

When `auto` is `false` for a matched file, the bot sends a prompt asking whether to forward it.

---

## Complete Example

```yaml
peers:
  gateway:
    host: bastion.university.edu
    user: admin

  gpu-1:
    host: gpu-node-1.internal
    user: researcher
    proxy_jump: gateway
    daemon_port: 9100
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
    admin_users:
      - 9876543210987654321

  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users:
      - 987654321
    admin_users:
      - 987654321

  lark:
    app_id: ${LARK_APP_ID}
    app_secret: ${LARK_APP_SECRET}
    allowed_chats:
      - "oc_abcdef1234567890"

  webui:
    enabled: false
    port: 8080
    host: 127.0.0.1

default_mode: auto

tool_batch_size: 15

skills:
  shared_dir: ./skills
  sync_on_start: true

daemon:
  install_dir: ~/.codecast/daemon
  auto_deploy: true
  log_file: ~/.codecast/daemon.log

file_pool:
  max_size: 1073741824
  pool_dir: ~/.codecast/file-pool
  remote_dir: /tmp/codecast/files

file_forward:
  enabled: false
  download_dir: ~/.codecast/downloads
  default_max_size: 5242880
  default_auto: false
  rules: []
```
