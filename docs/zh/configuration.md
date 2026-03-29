# 配置指南

Codecast 从 YAML 文件中读取配置。文件查找顺序如下：

1. 作为命令行参数提供的路径：`codecast /path/to/config.yaml`
2. `~/.codecast/config.yaml`（推荐位置）
3. 当前工作目录下的 `./config.yaml`（开发时的备用方案）

复制示例配置文件开始使用：

```bash
mkdir -p ~/.codecast
cp /path/to/codecast/config.example.yaml ~/.codecast/config.yaml
```

## 环境变量展开

所有字符串值都支持 `${ENV_VAR}` 语法。如果变量未设置，表达式将保持原样不变。

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}
```

路径值同样支持 `~` 展开（例如 `~/.ssh/id_rsa` 会展开为 `/home/user/.ssh/id_rsa`）。

密码可以使用 `file:` 前缀从文件中读取：

```yaml
password: file:~/.secrets/my-password
```

文件中只应包含密码文本（首尾空白字符会被自动去除）。

---

## `peers`

定义 Codecast 可以连接的远程机器（对端）。每个键是机器 ID，用于 `/start gpu-1 /path` 等命令中。

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

### 对端传输类型

| `transport` 值 | 说明 |
|---|---|
| `ssh` | 通过 SSH 隧道连接（默认） |
| `http` | 直接通过 HTTP/HTTPS 连接（不使用 SSH） |
| `local` | 本地机器，跳过 SSH 隧道 |

对于 `ssh` 传输方式（默认），可使用以下字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `host` | string | （机器 ID） | 远程机器的主机名或 IP。如果省略，默认使用机器 ID。 |
| `user` | string | `$USER` | SSH 用户名。 |
| `ssh_key` | string | （无） | SSH 私钥路径。如果省略，使用 ssh-agent 或默认密钥。 |
| `port` | int | `22` | SSH 端口。 |
| `proxy_jump` | string | （无） | 跳板机的机器 ID（必须已在 `peers` 下定义）。 |
| `proxy_command` | string | （无） | 用于高级代理配置的 SSH ProxyCommand 字符串。 |
| `password` | string | （无） | SSH 密码，或 `file:/path/to/file` 从文件中读取。 |
| `daemon_port` | int | `9100` | 守护进程在远程机器上监听的端口，绑定到 `127.0.0.1`。 |
| `default_paths` | list[string] | `[]` | 常用项目路径列表，用于 Discord 自动补全，并显示在 `/ls machine` 中。 |

对于 `http` 传输方式，使用以下字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `address` | string | 守护进程的完整 URL（如 `https://myserver.example.com:9100`） |
| `token` | string | 认证 token |
| `tls_fingerprint` | string | 可选的 TLS 证书指纹（用于固定证书） |

### ProxyJump 示例

对于位于堡垒机后面的机器：

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

`gateway` 对端仅作为跳板机使用，不需要配置 `default_paths`。

---

## `bot`

配置机器人连接。Codecast 启动时至少需要配置一个机器人。

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `token` | string | （必填） | Discord 机器人 token。 |
| `allowed_channels` | list[int] | `[]` | 机器人响应的频道 ID 列表。为空表示所有频道。 |
| `admin_users` | list[int] | `[]` | 允许使用 `/update` 和 `/restart` 的 Discord 用户 ID。 |
| `command_prefix` | string | `"/"` | 文本命令的前缀。斜杠命令始终使用 `/`。 |

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `token` | string | （必填） | 来自 @BotFather 的 Telegram 机器人 token。 |
| `allowed_users` | list[int] | `[]` | 允许使用机器人的 Telegram 用户 ID。为空表示所有用户。 |
| `allowed_chats` | list[int] | `[]` | 允许的群组或频道 ID。为空表示所有会话。 |
| `admin_users` | list[int] | `[]` | 允许使用 `/update` 和 `/restart` 的用户 ID。 |

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `app_id` | string | （必填） | 飞书应用的 App ID。 |
| `app_secret` | string | （必填） | 飞书应用的 App Secret。 |
| `allowed_chats` | list[string] | `[]` | 允许的飞书会话 ID。为空表示所有会话。 |
| `admin_users` | list[string] | `[]` | 允许使用 `/update` 和 `/restart` 的飞书用户 open ID。 |
| `use_cards` | bool | `true` | 为问题和工具显示使用交互卡片。 |

### `bot.webui`

在聊天机器人之外额外启用基于浏览器的 Web UI。

```yaml
bot:
  webui:
    enabled: true
    port: 8080
    host: 127.0.0.1
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 启用 Web UI。 |
| `port` | int | `8080` | 监听端口。 |
| `host` | string | `"127.0.0.1"` | 绑定地址。使用 `0.0.0.0` 允许外部访问。 |

---

## `default_mode`

```yaml
default_mode: auto
```

新会话的默认权限模式，可在每个会话中通过 `/mode` 修改。

| 模式 | 说明 |
|---|---|
| `auto` | 完全自动化。AI 可以无需确认地读取、写入和执行任何内容。在机器人输出中显示为"bypass"。 |
| `code` | 自动接受文件编辑，在执行 shell 命令前提示确认。 |
| `plan` | 只读分析。AI 不能进行任何修改。 |
| `ask` | 确认所有工具调用。 |

---

## `tool_batch_size`

```yaml
tool_batch_size: 15
```

连续 tool_use 事件（文件读取、shell 命令等）被压缩为单条摘要消息的数量阈值。可减少大型操作期间的聊天噪音。默认值为 `15`。

---

## `skills`

```yaml
skills:
  shared_dir: ./skills
  sync_on_start: true
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `shared_dir` | string | `"./skills"` | 包含待同步共享技能的本地目录。应包含 `CLAUDE.md` 和/或 `.claude/skills/`。 |
| `sync_on_start` | bool | `true` | 使用 `/start` 创建新会话时同步技能。远程已有文件不会被覆盖。 |

---

## `daemon`

控制 Codecast 守护进程（单一静态 Rust 二进制文件）在远程机器上的部署和管理方式。

```yaml
daemon:
  install_dir: ~/.codecast/daemon
  auto_deploy: true
  log_file: ~/.codecast/daemon.log
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `install_dir` | string | `"~/.codecast/daemon"` | 在远程机器上安装守护进程二进制文件的目录。 |
| `auto_deploy` | bool | `true` | 如果守护进程不存在或版本不匹配，自动通过 SCP 部署。 |
| `log_file` | string | `"~/.codecast/daemon.log"` | 远程机器上守护进程日志文件的路径。 |

远程机器不需要 Node.js 或 npm。守护进程是一个无外部运行时依赖的自包含二进制文件。

---

## `file_pool`

控制上传到机器人聊天的文件如何暂存以供 AI 会话使用。

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_size` | int | `1073741824`（1 GB） | 本地文件池的最大总大小（字节）。 |
| `pool_dir` | string | `"~/.codecast/file-pool"` | 缓存上传文件的本地目录。 |
| `remote_dir` | string | `"/tmp/codecast/files"` | 文件传递给 AI 之前上传到远程机器的目录。 |
| `allowed_types` | list[string] | （见上方） | 接受文件的 MIME 类型模式。支持通配符（如 `image/*`）。 |

---

## `file_forward`

控制当 AI 响应中包含匹配文件路径时，自动将文件从远程机器转发到聊天。

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 启用文件转发。 |
| `download_dir` | string | `"~/.codecast/downloads"` | 临时存储已下载文件的本地目录。 |
| `default_max_size` | int | `5242880`（5 MB） | 转发文件的默认最大大小（字节）。 |
| `default_auto` | bool | `false` | 自动转发匹配的文件而无需提示。 |
| `rules` | list | `[]` | 按模式覆盖的规则列表。 |

`rules` 中每条规则的字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pattern` | string | 与文件路径匹配的 glob 模式（如 `*.png`、`*.log`）。 |
| `max_size` | int | 该规则的最大文件大小（字节）。 |
| `auto` | bool | 为 `true` 时自动发送文件；为 `false` 时显示确认提示。 |

当匹配文件的 `auto` 为 `false` 时，机器人会发送提示询问是否转发该文件。

---

## 完整示例

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
