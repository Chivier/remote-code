# 快速开始

本指南将带你从零开始完成 Codecast 的部署。完成后，你将拥有一个运行在 Discord、Telegram 或飞书上的机器人，用于与远程机器上的 AI 命令行工具进行交互。

## 前提条件

### 本地机器（Head Node）

- Python 3.10 或更高版本
- pip（Python 包管理器）
- 已为远程机器配置好 SSH 密钥（或密码认证）
- 至少一个平台的机器人 token：Discord、Telegram 或飞书

### 远程机器

- 可从本地机器通过 SSH 访问
- 至少安装并完成认证的 AI 命令行工具之一：
  - Claude CLI（`claude` 在 PATH 中）
  - Codex（`codex` 在 PATH 中）
  - Gemini CLI（`gemini` 在 PATH 中）
  - OpenCode（`opencode` 在 PATH 中）

远程机器不需要 Node.js 或 npm。Codecast 守护进程是一个单一的静态 Rust 二进制文件，通过 SCP 自动部署。

## 机器人配置

### Discord

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)。
2. 创建一个新应用，然后在"Bot"下创建机器人并复制 token。
3. 在"Privileged Gateway Intents"下启用"Message Content Intent"。
4. 在"OAuth2 > URL Generator"中，选择 `bot` 和 `applications.commands` 两个 scope，权限勾选：Send Messages、Manage Messages、Read Message History。
5. 使用生成的链接将机器人邀请到你的服务器。

### Telegram

1. 在 Telegram 中找到 [@BotFather](https://t.me/BotFather)。
2. 发送 `/newbot` 并按提示操作。
3. 复制 BotFather 提供的 token。

### 飞书（Lark）

1. 前往[飞书开放平台](https://open.feishu.cn/app)创建应用。
2. 在"权限管理"中添加：`im:message`、`im:message:send_as_bot`。
3. 在"事件订阅"中添加 `im.message.receive_v1` 事件。
4. 复制 App ID 和 App Secret。
5. 配置 webhook 端点，或使用飞书内置的机器人消息功能。

## 安装

### 方式一：从 PyPI 安装（推荐）

```bash
pip install codecast
```

此命令会安装所有依赖并提供 `codecast` 命令。

### 方式二：从源码安装

```bash
git clone https://github.com/Chivier/codecast.git
cd codecast
pip install -e .
```

`pip install -e .` 以可编辑模式安装包，并提供 `codecast` 命令。

### 手动构建守护进程（可选）

如果 `daemon.auto_deploy` 已启用（默认值），守护进程二进制文件会在首次连接时自动部署到远程机器。如果需要手动构建：

```bash
cargo build --release
```

输出的二进制文件位于 `target/release/codecast-daemon`。将其复制到本地机器的 `~/.codecast/daemon/codecast-daemon`，系统将从此处部署。

### 在远程机器上安装 AI 命令行工具

每台远程机器至少需要安装一个 AI 命令行工具。以 Claude CLI 为例：

```bash
# 在远程机器上执行
npm install -g @anthropic-ai/claude-code
claude auth login
```

验证命令行工具是否正常工作：

```bash
claude --print "Hello" --output-format stream-json
```

## 配置

### 1. 创建配置文件

配置文件的主要存放位置是 `~/.codecast/config.yaml`。创建目录并复制示例文件：

```bash
mkdir -p ~/.codecast
cp /path/to/codecast/config.example.yaml ~/.codecast/config.yaml
```

也可以将 `config.yaml` 放在当前工作目录中作为开发时的备用方案。

### 2. 设置环境变量

导出你的机器人 token：

```bash
export DISCORD_TOKEN="your-discord-bot-token"
export TELEGRAM_TOKEN="your-telegram-bot-token"
export LARK_APP_ID="your-lark-app-id"
export LARK_APP_SECRET="your-lark-app-secret"
```

配置值支持 `${ENV_VAR}` 语法，因此 token 不会硬编码在文件中。

### 3. 配置远程机器

编辑 `~/.codecast/config.yaml`，在 `peers:` 下添加你的机器：

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

有关所有可用选项（包括 Telegram、飞书、ProxyJump 和文件转发），请参阅[配置指南](./configuration.md)。

## 使用 TUI 向导完成首次配置

如果你是首次使用 Codecast，TUI（终端 UI）提供了一个交互式配置向导：

```bash
codecast tui
```

通过 TUI 可以配置机器、测试 SSH 连接并启动会话，无需直接编辑 YAML 文件。

## 运行 Codecast

启动 Head Node：

```bash
codecast
```

指定特定配置文件：

```bash
codecast /path/to/config.yaml
```

你应该会看到类似如下的输出：

```
INFO: Discord bot configured
INFO: Telegram bot configured
INFO: Codecast started with 2 bot(s)
INFO: Peers: gpu-1
INFO: Default mode: auto
```

## 开始第一个会话

1. 打开 Discord、Telegram 或飞书。
2. 在允许的频道或聊天中，使用 `/start` 命令：

   ```
   /start gpu-1 /home/your-user/project-a
   ```

3. Codecast 将会：
   - 建立到 `gpu-1` 的 SSH 隧道
   - 如果守护进程尚未部署，则自动部署（auto-deploy）
   - 在远程机器上启动守护进程
   - 如果已配置，同步技能文件
   - 在项目目录中创建 AI 会话

4. 发送消息开始交互：

   ```
   这个项目中有哪些文件？
   ```

5. 响应将实时流式回传。

要使用 Claude 以外的特定 CLI 启动会话：

```
/start gpu-1 /home/your-user/project --cli codex
```

支持的 CLI 类型：`claude`、`codex`、`gemini`、`opencode`。

## 停止 Codecast

按 `Ctrl+C` 或向进程发送 `SIGTERM`。Head Node 将会：

1. 优雅地停止所有机器人
2. 关闭守护进程客户端的 HTTP 会话
3. 关闭所有 SSH 隧道
4. 取消待处理的任务

Head Node 关闭时不会销毁远程守护进程上的会话。之后可以用 `/resume` 恢复它们。

## 常见问题排查

**"Could not connect to machine"** -- 检查 SSH 主机、用户名和密钥是否正确。在终端运行 `ssh user@host` 测试连接。如果机器位于跳板机后面，请参阅[配置指南](./configuration.md)中的 `proxy_jump` 选项。

**"Daemon not found after deploy"** -- 检查远程机器上 `~/.codecast/daemon/` 是否存在，且二进制文件有执行权限。查看远程机器上的 `~/.codecast/daemon.log` 获取错误信息。

**"claude: command not found"** -- Claude CLI 未在远程机器上安装或不在 PATH 中。守护进程继承 SSH 会话的 PATH；请确保通过普通 SSH 登录时命令行工具可以访问。

**机器人没有响应** -- 确认机器人 token 有效，且频道或用户 ID 在允许列表中。Discord 请检查 Message Content Intent 是否已启用。

**会话卡住** -- 在聊天中使用 `/interrupt` 或 `/stop` 中断当前 AI 操作。使用 `/status` 检查队列状态。
