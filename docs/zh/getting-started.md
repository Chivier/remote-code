# 快速开始

本指南将帮助你从零开始部署和运行 Remote Code。

## 前提条件

### 本地环境（Head Node）

- **Python 3.11+**
- **pip** 包管理器
- **SSH 密钥** 或密码，用于连接远程机器
- **Discord Bot Token** 和/或 **Telegram Bot Token**

### 远程环境（Daemon 目标机器）

- **Node.js 18+**（需要在 PATH 中或通过 `node_path` 配置指定路径）
- **npm**（用于安装 Daemon 依赖）
- **Claude CLI**（`claude` 命令需在 PATH 中可用，通常安装在 `~/.local/bin/`）
- **SSH 服务** 已启用且可从本地访问

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/user/happy-moon.git
cd happy-moon
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

主要依赖包括：
- `asyncssh` — 异步 SSH 连接
- `aiohttp` — 异步 HTTP 客户端
- `discord.py` — Discord Bot SDK
- `python-telegram-bot` — Telegram Bot SDK
- `PyYAML` — YAML 配置文件解析

### 3. 创建配置文件

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，配置你的远程机器和 Bot token。详细配置说明请参考 [配置指南](./configuration.md)。

### 4. 配置 Bot Token

#### Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建一个新的 Application
3. 在 "Bot" 页面创建 Bot 并获取 Token
4. 在 "OAuth2" 页面生成邀请链接，确保勾选以下权限：
   - `bot` scope
   - `applications.commands` scope（用于斜杠命令）
   - Send Messages
   - Read Message History
   - Use Slash Commands
5. 将 Bot 邀请到你的 Discord 服务器
6. 在 "Bot" 页面启用 "Message Content Intent"

#### Telegram Bot

1. 在 Telegram 中找到 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建新 Bot
3. 获取 Bot Token

### 5. 设置环境变量

```bash
export DISCORD_TOKEN="your-discord-bot-token"
export TELEGRAM_TOKEN="your-telegram-bot-token"
```

或者直接在 `config.yaml` 中填写 token（不推荐，因为 token 是敏感信息）。

### 6. 配置远程机器

在 `config.yaml` 的 `machines` 部分配置你的远程机器：

```yaml
machines:
  my-gpu:
    host: gpu.example.com
    user: myuser
    ssh_key: ~/.ssh/id_rsa
    daemon_port: 9100
    default_paths:
      - /home/myuser/my-project
```

确保：
- SSH 密钥认证已配置好（或使用密码）
- 远程机器上已安装 Node.js 和 Claude CLI
- Claude CLI 已通过 `claude login` 完成认证

### 7. 构建 Daemon（可选）

如果启用了 `auto_deploy: true`（默认），系统会自动构建和部署 Daemon。

手动构建：

```bash
cd daemon
npm install
npm run build
cd ..
```

## 启动

```bash
python -m head.main
```

或指定配置文件路径：

```bash
python -m head.main /path/to/config.yaml
```

启动后你会看到类似以下日志：

```
2026-03-14 10:00:00 [remote-code] INFO: Discord bot configured
2026-03-14 10:00:00 [remote-code] INFO: Telegram bot configured
2026-03-14 10:00:00 [remote-code] INFO: Remote Code started with 2 bot(s)
2026-03-14 10:00:00 [remote-code] INFO: Machines: my-gpu
2026-03-14 10:00:00 [remote-code] INFO: Default mode: auto
2026-03-14 10:00:01 [head.bot_discord] INFO: Discord bot logged in as RemoteClaude#1234
2026-03-14 10:00:01 [head.bot_discord] INFO: Synced 12 slash command(s)
2026-03-14 10:00:01 [head.bot_telegram] INFO: Telegram bot started
```

## 首次使用

### 在 Discord 中

1. 在允许的频道中使用斜杠命令：

```
/start machine:my-gpu path:/home/myuser/my-project
```

2. 等待系统建立 SSH 隧道、部署 Daemon（首次需要几十秒）、创建会话
3. 看到 "Session started" 消息后，直接发送文字消息与 Claude 对话
4. 使用 `/exit` 分离会话，`/resume` 恢复会话

### 在 Telegram 中

1. 向 Bot 发送命令：

```
/start my-gpu /home/myuser/my-project
```

2. 之后直接发送消息与 Claude 对话

## 自动部署流程

首次连接到一台远程机器时，如果 `daemon.auto_deploy` 为 `true`，系统会自动执行以下步骤：

1. 在本地构建 Daemon 代码（`npm run build`）
2. 通过 SCP 将 `package.json`、`package-lock.json` 和 `dist/` 目录上传到远程机器
3. 在远程机器上执行 `npm install --production`
4. 使用 `nohup` 启动 Daemon 进程
5. 轮询健康检查端点直到 Daemon 就绪（最多 30 秒）

## 停止

按 `Ctrl+C` 或发送 `SIGTERM` 信号即可优雅关闭：

```
2026-03-14 10:30:00 [remote-code] INFO: Received SIGINT, shutting down...
2026-03-14 10:30:00 [remote-code] INFO: Cleaning up...
2026-03-14 10:30:00 [remote-code] INFO: Closing tunnel to my-gpu
2026-03-14 10:30:01 [remote-code] INFO: Remote Code stopped
```

系统会依次：停止所有 Bot → 关闭 HTTP 客户端 → 关闭所有 SSH 隧道 → 取消残余异步任务。

> **注意**：停止 Head Node 不会停止远程 Daemon 进程。Daemon 会继续在远程机器上运行，下次连接时可以复用。

## 常见问题

### Daemon 启动失败

- 检查远程机器上的 Node.js 版本（需要 18+）
- 检查 `~/.remote-code/daemon.log` 中的错误日志
- 确保 `claude` 命令在远程机器的 PATH 中

### SSH 连接失败

- 确认 SSH 密钥认证正常（手动 `ssh user@host` 测试）
- 如果需要跳板机，确保 `proxy_jump` 配置正确
- 检查防火墙是否允许 SSH 端口

### Claude CLI 认证

- 在远程机器上运行 `claude login` 完成认证
- 确保环境变量 `ANTHROPIC_API_KEY` 已设置（如果使用 API key 模式）

### Discord 命令不显示

- 斜杠命令同步可能需要几分钟
- 确保 Bot 有 `applications.commands` 权限
- 尝试在 Discord Developer Portal 中清除已注册的命令后重启
