# Bot 命令参考

Remote Code 在 Discord 和 Telegram 中提供一致的命令接口。Discord 支持斜杠命令（带自动补全），Telegram 使用标准命令语法。

## 命令一览

| 命令 | 说明 | 需要活跃会话 |
|------|------|:---:|
| `/start` | 创建新的 Claude 会话 | 否 |
| `/resume` | 恢复之前的会话 | 否 |
| `/new` | 在当前目录开启新会话（自动分离当前会话） | 是 |
| `/clear` | 销毁当前会话并在同目录重新开始 | 是 |
| `/ls` | 列出机器或会话 | 否 |
| `/exit` | 分离当前会话 | 是 |
| `/rm` | 销毁指定会话 | 否 |
| `/mode` | 切换权限模式 | 是 |
| `/rename` | 重命名当前会话（格式：词-词） | 是 |
| `/status` | 显示当前会话状态 | 是 |
| `/interrupt` | 中断 Claude 当前操作 | 是 |
| `/health` | 检查 Daemon 健康状态 | 否 |
| `/monitor` | 查看会话详情和队列状态 | 否 |
| `/add-machine` | 添加远程机器 | 否 |
| `/remove-machine` | 移除远程机器 | 否 |
| `/update` | 拉取最新代码并重启（仅管理员） | 否 |
| `/restart` | 重启 Head Node（仅管理员） | 否 |
| `/help` | 显示帮助信息 | 否 |

## 命令详解

### /start

创建一个新的 Claude 会话，连接到指定的远程机器和项目路径。

**语法**：
```
/start <machine_id> <path>
```

**参数**：
- `machine_id` — 远程机器 ID（在 config.yaml 中定义）
- `path` — 远程机器上的项目路径

**示例**：
```
/start gpu-1 /home/user/my-project
```

**行为**：
1. 建立到目标机器的 SSH 隧道（如果不存在）
2. 如果 Daemon 未运行，自动部署并启动
3. 同步技能文件到远程项目目录
4. 在 Daemon 上创建新会话
5. 将当前频道/聊天绑定到该会话

如果当前频道已有活跃会话，旧会话会被自动分离（detach）。

**Discord 特性**：
- `machine` 参数支持自动补全（基于 config.yaml 中的机器列表，排除纯跳板机）
- `path` 参数支持自动补全（基于所选机器的 `default_paths`）

---

### /resume

恢复之前分离或断开的会话。

**语法**：
```
/resume <session_id>
```

**参数**：
- `session_id` — Daemon 会话 ID（可以从 `/ls session` 的输出中获取）

**示例**：
```
/resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**行为**：
1. 在会话记录中查找指定的会话
2. 重新建立 SSH 隧道到对应的机器
3. 在 Daemon 上恢复会话（如果可能使用 SDK 会话 ID 恢复完整上下文）
4. 将当前频道绑定到该会话

如果直接恢复失败，系统可能会创建一个新会话并注入历史上下文（fallback 模式）。

---

### /new

在当前会话所在的目录开启一个全新的 Claude 会话，自动分离当前会话。

**语法**：
```
/new
```

**行为**：
1. 当前会话被分离（不销毁）
2. 在同一机器和同一路径上创建新会话
3. 新会话绑定到当前频道

相当于先执行 `/exit`，再以相同的机器和路径执行 `/start`，无需重新输入连接信息。

---

### /clear

销毁当前会话并立即在同一目录启动新会话。

**语法**：
```
/clear
```

**行为**：
1. 当前会话的 Claude 进程被终止
2. 会话记录被标记为 destroyed
3. 在同一机器和路径上创建新会话
4. 新会话绑定到当前频道

与 `/new` 的区别：`/clear` 会彻底销毁旧会话，而 `/new` 只是分离旧会话（旧会话可通过 `/resume` 恢复）。

---

### /ls

列出机器或会话信息。

**语法**：
```
/ls machine          # 列出所有机器
/ls session [machine] # 列出会话，可选按机器过滤
```

**子命令**：

#### /ls machine

列出所有配置的远程机器及其状态。

输出格式：
```
Machines:
🟢 gpu-1 (gpu1.example.com) ⚡
  Paths: `/home/user/project-a`, `/home/user/project-b`
🔴 gpu-2 (gpu2.lab.internal) 💤
```

状态图标：
- 🟢 在线 / 🔴 离线
- ⚡ Daemon 运行中 / 💤 Daemon 未运行

纯跳板机（无 `default_paths` 且被其他机器用作 `proxy_jump`）不会显示在列表中。

#### /ls session [machine]

列出所有会话或指定机器上的会话。

输出格式：
```
Sessions:
● `a1b2c3d4...` gpu-1:/home/user/project-a [bypass] (active)
○ `e5f67890...` gpu-1:/home/user/project-b [code] (detached)
```

状态图标：
- `●` 活跃 / `○` 已分离 / `✕` 已销毁

**Discord 特性**：
- `target` 参数有下拉选择框：`machine` 或 `session`
- `machine` 参数支持自动补全

---

### /exit

分离（detach）当前频道的活跃会话。会话不会被销毁，可以稍后使用 `/resume` 恢复。

**语法**：
```
/exit
```

**行为**：
1. 将当前会话状态从 `active` 改为 `detached`
2. 记录到 `session_log` 表中
3. 解除当前频道与会话的绑定
4. 返回会话 ID 供后续恢复使用

---

### /rm

销毁指定机器和路径上的会话。

**语法**：
```
/rm <machine_id> <path>
```

**参数**：
- `machine_id` — 机器 ID
- `path` — 项目路径

**示例**：
```
/rm gpu-1 /home/user/my-project
```

**行为**：
1. 查找匹配的所有会话（活跃和已分离的）
2. 在 Daemon 上销毁这些会话（终止 Claude 进程）
3. 在 SessionRouter 中标记为 `destroyed`

---

### /mode

切换当前会话的权限模式。

**语法**：
```
/mode <mode>
```

**参数**：
- `mode` — 权限模式：`auto`、`code`、`plan` 或 `ask`

**四种模式说明**：

| 模式 | 显示名称 | CLI 标志 | 说明 |
|------|---------|----------|------|
| `auto` | bypass | `--dangerously-skip-permissions` | 完全自动，跳过所有权限确认 |
| `code` | code | — | 自动接受文件编辑，bash 命令需确认 |
| `plan` | plan | — | 只读分析模式，不修改任何文件 |
| `ask` | ask | — | 所有操作都需要确认 |

> **注意**：`auto` 模式在显示时会显示为 `bypass`，因为它使用了 `--dangerously-skip-permissions` 标志。输入 `bypass` 也会被自动映射到 `auto`。

**示例**：
```
/mode auto     # 切换到完全自动模式
/mode plan     # 切换到只读分析模式
```

**Discord 特性**：
- `mode` 参数有下拉选择框，每个选项附带说明文字

---

### /rename

重命名当前会话。

**语法**：
```
/rename <新名称>
```

**参数**：
- `新名称` — 必须为 `词-词` 格式（形容词-名词，例如 `smooth-dove`、`fast-hawk`）

**示例**：
```
/rename fast-hawk
```

新名称存储在会话注册表中，可以在 `/resume` 命令中代替 UUID 使用。

---

### /status

显示当前频道活跃会话的详细状态。

**语法**：
```
/status
```

**输出示例**：
```
Session Status
Machine: gpu-1
Path: /home/user/my-project
Mode: bypass
Status: active
Session ID: a1b2c3d4e5f6...
Queue: 0 pending messages
Buffered: 0 responses
```

---

### /interrupt

中断 Claude 当前正在进行的操作。向 Claude CLI 进程发送 SIGTERM 信号。

**语法**：
```
/interrupt
```

**行为**：
- 如果 Claude 正在处理请求，终止当前进程并清空消息队列
- 如果 Claude 空闲，提示 "Claude is not currently processing any request."
- 进程被终止后，会话保持可用状态，可以继续发送新消息

---

### /health

检查指定机器上 Daemon 的健康状态。

**语法**：
```
/health [machine_id]
```

**参数**：
- `machine_id`（可选） — 机器 ID。不指定时检查当前会话的机器，或检查所有已连接的机器

**输出示例**：
```
Daemon Health - gpu-1
Status: OK
Uptime: 2h15m30s
Sessions: 3 (idle: 2, busy: 1)
Memory: 85MB RSS, 42/65MB heap
Node: v20.11.0 (PID: 12345)
```

**Discord 特性**：
- `machine` 参数支持自动补全

---

### /monitor

查看指定机器上所有会话的详细信息和队列状态。

**语法**：
```
/monitor [machine_id]
```

**参数**：
- `machine_id`（可选） — 机器 ID。不指定时查看当前会话的机器，或查看所有已连接的机器

**输出示例**：
```
Monitor - gpu-1 (uptime: 2h15m30s, 2 session(s))

● `a1b2c3d4...` idle [bypass | claude-sonnet-4-20250514]
  Path: /home/user/project-a
  Client: connected | Queue: 0 pending, 0 buffered

◉ `e5f67890...` busy [code | claude-sonnet-4-20250514]
  Path: /home/user/project-b
  Client: connected | Queue: 1 pending, 0 buffered
```

状态图标：
- `●` 空闲（idle）
- `◉` 繁忙（busy）
- `✕` 错误或已销毁

**Discord 特性**：
- `machine` 参数支持自动补全

---

### /add-machine

添加新的远程机器到配置中。

**语法**：
```
/add-machine <名称> [主机] [用户] [选项]
/add-machine --from-ssh
```

**参数**：
- `名称` — 机器的简短标识符（用于其他命令）
- `主机`（可选）— IP 地址或主机名（可从 SSH 配置中自动解析）
- `用户`（可选）— SSH 用户名（可从 SSH 配置中自动解析）
- `选项`（可选）— 额外的 SSH 选项（端口、跳板机等）
- `--from-ssh` — 从 `~/.ssh/config` 浏览并导入

**示例**：
```
/add-machine gpu-3 10.0.1.52 alice
/add-machine gpu-3 --from-ssh
```

机器配置会立即写入 `config.yaml`，首次 `/start` 时自动部署 Daemon。

---

### /remove-machine

从配置中移除一台远程机器。

**语法**：
```
/remove-machine <machine_id>
```

**参数**：
- `machine_id` — 要移除的机器 ID

**示例**：
```
/remove-machine gpu-3
```

如果该机器上存在活跃或已分离的会话，命令会要求确认。机器配置会从 `config.yaml` 中删除。

---

### /update

拉取最新代码并重启 Head Node。**仅管理员可用。**

**语法**：
```
/update
```

**行为**：
1. 在项目目录执行 `git pull --ff-only`
2. 通过 `os.execv()` 原地替换运行中的进程（保持 PID 不变）
3. 重启完成后发送确认消息

需要在配置中将你的用户 ID 添加到 `admin_users` 列表。

---

### /restart

重启 Head Node，不拉取新代码。**仅管理员可用。**

**语法**：
```
/restart
```

通过 `os.execv()` 原地替换运行中的进程。适用于加载配置变更或从异常状态恢复。需要在配置中将你的用户 ID 添加到 `admin_users` 列表。

---

### /help

显示所有可用命令的帮助信息。

**语法**：
```
/help
```

## 消息转发

除了命令之外，所有非 `/` 开头的消息都会被直接转发给当前活跃的 Claude 会话。

**流式响应显示**：
- 使用 SSE 接收 Claude 的流式回复
- 每 1.5 秒更新一次消息内容（在末尾显示 `▌` 光标）
- 当消息长度超过 1800 字符时，自动分割为新消息
- Discord 单条消息限制 2000 字符，Telegram 限制 4096 字符
- 代码块不会被中间截断

**并发保护**：同一频道不允许同时发送多条消息。如果 Claude 正在处理，新消息会提示 "Claude is still processing. Please wait..."

**Discord 心跳**：在 Claude 处理期间，每 25 秒发送一次状态更新消息，显示 Claude 当前正在做什么（思考中 / 使用工具 / 写回复），避免 Discord 的 3 分钟超时感。

## Discord 斜杠命令

Discord 版本使用 `app_commands`（斜杠命令），相比文本命令有以下优势：

- **自动补全** — 机器名和路径会出现补全建议
- **参数类型检查** — 系统自动验证参数格式
- **下拉选择** — `mode` 和 `target` 等参数有预定义选项
- **延迟响应** — 部分命令使用 `defer()` 避免 Discord 的 3 秒交互超时
- **打字指示器** — Claude 处理期间显示 "Bot is typing..."
