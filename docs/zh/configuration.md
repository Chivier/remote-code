# 配置指南

Remote Code 使用 YAML 配置文件来定义远程机器、Bot 设置和系统行为。默认读取项目根目录下的 `config.yaml`。

## 配置文件示例

```yaml
machines:
  gpu-1:
    host: gpu1.example.com
    user: your-ssh-user
    ssh_key: ~/.ssh/id_rsa
    port: 22
    daemon_port: 9100
    node_path: /usr/local/bin/node
    default_paths:
      - /home/your-user/project-a
      - /home/your-user/project-b

  gpu-2:
    host: gpu2.lab.internal
    user: your-ssh-user
    proxy_jump: gpu-1
    daemon_port: 9100

bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels:
      - 123456789012345678
    command_prefix: "/"

  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users:
      - 123456789

default_mode: auto

skills:
  shared_dir: ./skills
  sync_on_start: true

daemon:
  install_dir: ~/.remote-code/daemon
  auto_deploy: true
  log_file: ~/.remote-code/daemon.log
```

## 环境变量展开

配置文件中的所有字符串值都支持 `${VAR}` 语法引用环境变量。如果环境变量未定义，`${VAR}` 将保持原样不被替换。

```yaml
# 引用环境变量
token: ${DISCORD_TOKEN}

# 可以在任意字符串中使用
host: ${GPU_HOST}
ssh_key: ${HOME}/.ssh/id_rsa
```

路径类型的值还支持 `~` 展开为用户主目录。

## 配置字段详解

### machines

定义远程机器列表。每台机器使用一个唯一 ID 作为键名。

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `host` | string | 机器 ID | 否 | 主机名或 IP 地址。若不指定，使用机器 ID 作为主机名 |
| `user` | string | `$USER` | 否 | SSH 用户名。若不指定，使用当前系统用户名 |
| `ssh_key` | string | — | 否 | SSH 私钥路径（支持 `~` 展开）。不指定则使用 ssh-agent |
| `port` | int | `22` | 否 | SSH 端口号 |
| `proxy_jump` | string | — | 否 | 跳板机 ID（必须是 `machines` 中已定义的另一台机器） |
| `proxy_command` | string | — | 否 | SSH ProxyCommand 字符串 |
| `password` | string | — | 否 | SSH 密码。支持 `file:` 前缀从文件读取 |
| `daemon_port` | int | `9100` | 否 | 远程 Daemon RPC 监听端口 |
| `node_path` | string | `node` | 否 | 远程机器上 Node.js 可执行文件的路径 |
| `default_paths` | list[str] | `[]` | 否 | 常用项目路径列表（用于 Discord 自动补全） |

#### 跳板机（ProxyJump）

当远程机器无法从本地直接访问时，可以通过跳板机连接：

```yaml
machines:
  gateway:
    host: gateway.example.com
    user: admin
    ssh_key: ~/.ssh/id_rsa

  internal-gpu:
    host: 192.168.1.100
    user: researcher
    proxy_jump: gateway    # 通过 gateway 跳转
    daemon_port: 9100
    default_paths:
      - /data/projects/my-project
```

**注意**：仅作为跳板机使用且没有 `default_paths` 的机器，在 `/ls machine` 列表中会被自动过滤掉。

#### 密码认证

密码可以直接写在配置文件中，也可以通过 `file:` 前缀指向一个密码文件：

```yaml
machines:
  my-server:
    host: server.example.com
    user: myuser
    password: my-secret-password     # 直接写入（不推荐）

  another-server:
    host: server2.example.com
    user: myuser
    password: file:~/.ssh/server2.pass  # 从文件读取（推荐）
```

密码文件应只包含密码本身（可含尾部换行符，会被自动去除）。

### bot

Bot 平台配置。可以同时启用 Discord 和 Telegram，也可以只启用其中一个。至少需要配置一个 Bot，否则程序会报错退出。

#### bot.discord

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `token` | string | — | 是 | Discord Bot Token |
| `allowed_channels` | list[int] | `[]` | 否 | 允许使用的频道 ID 列表。空列表表示允许所有频道 |
| `command_prefix` | string | `"/"` | 否 | 命令前缀（主要用于非斜杠命令模式） |

#### bot.telegram

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `token` | string | — | 是 | Telegram Bot Token |
| `allowed_users` | list[int] | `[]` | 否 | 允许使用的用户 ID 列表。空列表表示允许所有用户 |

### default_mode

新会话的默认权限模式。

| 值 | CLI 标志 | 说明 |
|------|----------|------|
| `auto` | `--dangerously-skip-permissions` | 完全自动模式（bypass），跳过所有权限确认 |
| `code` | — | 自动接受文件编辑，需确认 bash 命令 |
| `plan` | — | 只读分析模式 |
| `ask` | — | 所有操作都需要确认 |

默认值：`auto`

> **安全提示**：`auto` 模式会跳过所有权限检查，Claude 可以自由执行任何操作（包括运行 bash 命令和修改文件）。在可信环境中使用此模式可以获得最佳的自动化体验，但请确保项目目录和环境是安全的。

### skills

技能同步配置。技能文件会在创建会话时从本地同步到远程项目目录。

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `shared_dir` | string | `./skills` | 否 | 本地技能目录路径 |
| `sync_on_start` | bool | `true` | 否 | 创建会话时是否自动同步技能 |

#### 技能目录结构

```
skills/
├── CLAUDE.md                # 项目级 Claude 指令文件
└── .claude/
    └── skills/
        ├── skill-a.md       # 自定义技能文件
        └── skill-b.md
```

同步规则：
- `CLAUDE.md` — 仅在远程目标目录不存在同名文件时才会复制（不覆盖已有文件）
- `.claude/skills/` — 目录下的文件逐个复制，已存在的文件不会被覆盖

### daemon

Daemon 部署配置。

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `install_dir` | string | `~/.remote-code/daemon` | 否 | Daemon 在远程机器上的安装目录 |
| `auto_deploy` | bool | `true` | 否 | 是否自动部署 Daemon 到远程机器 |
| `log_file` | string | `~/.remote-code/daemon.log` | 否 | Daemon 日志文件路径（在远程机器上） |

当 `auto_deploy` 为 `true` 时，如果远程机器上没有 Daemon 代码（`dist/server.js` 不存在或 `node_modules` 目录缺失），系统会自动：

1. 在本地构建 Daemon（如果 `dist/` 目录不存在）
2. 通过 SCP 上传 `package.json`、`package-lock.json` 和 `dist/` 目录
3. 在远程执行 `npm install --production`

## 完整配置数据类型参考

```python
@dataclass
class MachineConfig:
    id: str
    host: str
    user: str
    ssh_key: Optional[str] = None
    port: int = 22
    proxy_jump: Optional[str] = None
    proxy_command: Optional[str] = None
    password: Optional[str] = None
    daemon_port: int = 9100
    node_path: Optional[str] = None
    default_paths: list[str] = field(default_factory=list)

@dataclass
class DiscordConfig:
    token: str
    allowed_channels: list[int] = field(default_factory=list)
    command_prefix: str = "/"

@dataclass
class TelegramConfig:
    token: str
    allowed_users: list[int] = field(default_factory=list)

@dataclass
class BotConfig:
    discord: Optional[DiscordConfig] = None
    telegram: Optional[TelegramConfig] = None

@dataclass
class SkillsConfig:
    shared_dir: str = "./skills"
    sync_on_start: bool = True

@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.remote-code/daemon"
    auto_deploy: bool = True
    log_file: str = "~/.remote-code/daemon.log"

@dataclass
class Config:
    machines: dict[str, MachineConfig]
    bot: BotConfig
    default_mode: str = "auto"
    skills: SkillsConfig
    daemon: DaemonDeployConfig
```
