# 机器人命令参考

本文档说明 Discord、Telegram 和飞书机器人中所有可用的命令。

## 命令汇总

| 命令 | 参数 | 说明 |
|---|---|---|
| `/start` | `<machine> <path> [--cli <type>]` | 启动新的 AI 会话 |
| `/resume` | `<session_name_or_id>` | 恢复之前分离的会话 |
| `/new` | 无 | 在同一目录启动新会话 |
| `/clear` | 无 | 销毁当前会话并在同目录重新开始 |
| `/exit` | 无 | 分离当前会话 |
| `/stop` | 无 | 中断 AI 当前操作 |
| `/interrupt` | 无 | 中断 AI 当前操作（/stop 的别名） |
| `/ls` | `machine` 或 `session [machine]` | 列出机器或会话 |
| `/rm-session` | `<name_or_id>` | 按名称或 ID 销毁特定会话 |
| `/rm` | `<machine> <path>` | 销毁某台机器某路径下的所有会话 |
| `/mode` | `<auto\|code\|plan\|ask>` | 切换权限模式 |
| `/model` | `<model_name>` | 切换当前会话使用的 AI 模型 |
| `/tool-display` | `<timer\|append\|batch>` | 切换工具调用的显示方式 |
| `/rename` | `<new_name>` | 重命名当前会话 |
| `/status` | 无 | 显示当前会话信息 |
| `/health` | `[machine]` | 检查守护进程健康状态 |
| `/monitor` | `[machine]` | 查看会话详情和队列 |
| `/add-machine` | `<name> [host] [user]` | 添加远程机器 |
| `/remove-machine` | `<machine>` | 移除远程机器 |
| `/update` | 无 | Git pull 并重启（仅管理员） |
| `/restart` | 无 | 重启 Head Node（仅管理员） |
| `/help` | 无 | 显示可用命令 |

---

## `/start`

在远程机器上启动新的 AI 会话。

**用法：**

```
/start <machine_id> <path> [--cli <type>]
```

**参数：**

| 参数 | 说明 |
|---|---|
| `machine_id` | config.yaml 中定义的远程机器 ID |
| `path` | 远程机器上项目目录的绝对路径 |
| `--cli <type>` | 使用的 AI 命令行工具：`claude`、`codex`、`gemini` 或 `opencode`（默认：`claude`） |

**简写标志：** 可以用 `--codex`、`--gemini`、`--opencode` 代替 `--cli <type>`。

**示例：**

```
/start gpu-1 /home/user/my-project
/start gpu-1 /home/user/my-project --cli codex
/start gpu-1 /home/user/my-project --gemini
```

**执行过程：**

1. 建立到该机器的 SSH 隧道（如果尚未建立）。
2. 如果守护进程未运行，部署并启动守护进程。
3. 如果已配置，将技能文件同步到项目目录。
4. 在守护进程上创建新的 AI 会话。
5. 在本地数据库中注册该会话。
6. 发送确认消息，显示会话名称和当前模式。

会话名称自动以形容词-名词格式生成，例如 `bright-falcon` 或 `smooth-dove`。可以用 `/rename` 重命名会话。

**Discord：** 斜杠命令，`machine` 参数（来自已配置机器）和 `path` 参数（来自 config 中的 `default_paths`）均支持自动补全。

---

## `/resume`

恢复之前分离的会话。

**用法：**

```
/resume <session_name_or_id>
```

**参数：**

| 参数 | 说明 |
|---|---|
| `session_name_or_id` | 会话名称（如 `bright-falcon`）或守护进程 UUID |

**示例：**

```
/resume bright-falcon
/resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**执行过程：**

1. 在本地数据库中按名称或 ID 查找会话。
2. 建立到会话所在机器的 SSH 隧道。
3. 通知守护进程恢复该会话。
4. 将该会话重新注册为当前频道的活跃会话。
5. 后续消息将继续原有对话上下文。

---

## `/new`

在与当前会话相同的目录启动新的 AI 会话，自动分离当前会话。

**用法：**

```
/new
```

相当于先执行 `/exit`，再以相同的机器、路径和 CLI 类型执行 `/start`。适用于在不重新输入连接信息的情况下获得干净的上下文环境。

---

## `/clear`

销毁当前会话并立即在同一目录启动新会话。

**用法：**

```
/clear
```

与 `/new` 不同，旧会话会被完全销毁而非分离。

---

## `/exit`

分离当前会话，不销毁它。

**用法：**

```
/exit
```

远程机器上的 AI 进程继续运行。之后可以用会话名称通过 `/resume` 重新连接。

**示例输出：**

```
Detached from session on gpu-1:/home/user/project
Use /resume bright-falcon to reconnect.
```

---

## `/stop` 和 `/interrupt`

中断 AI 当前的操作。

**用法：**

```
/stop
/interrupt
```

两个命令等效，均会：

1. 向正在运行的 AI 进程发送中断信号。
2. 清空消息队列。
3. 会话保持活跃，可以继续发送消息。

**输出：**

- 如果 AI 正在处理中："Interrupted current operation."
- 如果 AI 处于空闲："No active operation to interrupt."

---

## `/ls`

列出机器或会话。

**用法：**

```
/ls machine
/ls session [machine_id]
```

**示例：**

```
/ls machine
/ls session
/ls session gpu-1
```

**机器列表输出：**

```
Machines:
  gpu-1 (gpu1.example.com) [online, daemon running]
    Paths: /home/user/project-a, /home/user/project-b
  gpu-2 (gpu2.lab.internal) [offline]
```

**会话列表输出：**

```
Sessions:
  bright-falcon  gpu-1:/home/user/project  [bypass] active
  smooth-dove    gpu-1:/home/user/other    [code]   detached
```

---

## `/rm-session`

按名称或 ID 销毁特定会话。

**用法：**

```
/rm-session <name_or_id>
```

**示例：**

```
/rm-session bright-falcon
/rm-session a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

该命令会终止该会话的 AI 进程，并在数据库中将其标记为已销毁。

---

## `/rm`

销毁某台机器某路径下匹配的所有会话。

**用法：**

```
/rm <machine_id> <path>
```

**示例：**

```
/rm gpu-1 /home/user/project
```

指定机器与路径组合下所有活跃和已分离的会话都会被销毁。

---

## `/mode`

切换当前会话的权限模式。

**用法：**

```
/mode <auto|code|plan|ask>
```

| 模式 | 说明 |
|---|---|
| `auto` | 完全自动化。AI 可以无需确认地读取、写入和执行任何内容。在机器人输出中显示为"bypass"。 |
| `code` | 自动接受文件编辑。AI 在运行 shell 命令前需要确认。 |
| `plan` | 只读分析。AI 可以读取文件但不能进行任何更改。 |
| `ask` | 确认所有操作。每次工具调用都需要审批。 |

**示例：**

```
/mode plan
```

**Discord：** 下拉选择，每种模式附有描述文字。

---

## `/model`

切换当前会话使用的 AI 模型。

**用法：**

```
/model <model_name>
```

**示例：**

```
/model claude-sonnet-4-20250514
/model claude-opus-4-20250514
```

模型切换在下一条消息发送时生效。使用 `/status` 确认当前活跃的模型。

---

## `/tool-display`

切换 AI 工作时工具调用（文件读取、shell 命令等）的显示方式。

**用法：**

```
/tool-display <timer|append|batch>
```

| 模式 | 说明 |
|---|---|
| `timer` | AI 工作时显示"Working Xs"计时器。所有结果最后一并发送。此为默认模式。 |
| `append` | 逐步展示每次工具调用，实时呈现执行过程。 |
| `batch` | 汇总所有工具调用，最后发送一条摘要消息。 |

**示例：**

```
/tool-display timer
```

---

## `/rename`

重命名当前会话。

**用法：**

```
/rename <new_name>
```

**参数：**

| 参数 | 说明 |
|---|---|
| `new_name` | `词-词` 格式的新名称（如 `fast-hawk`、`smooth-dove`） |

**示例：**

```
/rename fast-hawk
```

新名称会存储在会话注册表中，可在 `/resume` 命令中使用。

---

## `/status`

显示当前会话的状态和队列统计信息。

**用法：**

```
/status
```

**示例输出：**

```
Session: bright-falcon
Machine: gpu-1
Path: /home/user/project
Mode: bypass
Status: active
CLI: claude
Model: claude-sonnet-4-20250514
Queue: 0 pending messages
Buffered: 0 responses
```

---

## `/health`

检查远程机器上守护进程的健康状态。

**用法：**

```
/health [machine_id]
```

如果未指定机器，则检查当前会话所在机器，或检查所有已连接机器。

**示例输出：**

```
Daemon Health - gpu-1
Status: OK
Uptime: 2h 15m 30s
Sessions: 3 (idle: 2, busy: 1)
```

---

## `/monitor`

查看远程机器上会话详情和队列状态。

**用法：**

```
/monitor [machine_id]
```

**示例输出：**

```
Monitor - gpu-1 (uptime: 2h 15m 30s, 2 session(s))

  bright-falcon  idle [bypass | claude-sonnet-4-20250514]
    Path: /home/user/project
    Client: connected | Queue: 0 pending, 0 buffered

  smooth-dove  busy [code | claude-sonnet-4-20250514]
    Path: /home/user/other
    Client: disconnected | Queue: 1 pending, 5 buffered
```

---

## `/add-machine`

向配置中添加新的远程机器。

**用法：**

```
/add-machine <name> [host] [user]
/add-machine --from-ssh
```

**示例：**

```
/add-machine gpu-3 10.0.1.52 alice
/add-machine gpu-3 --from-ssh
```

`--from-ssh` 选项会读取 `~/.ssh/config` 并提供交互式主机选择界面进行导入。机器配置会立即写入 `config.yaml`，首次 `/start` 时自动部署守护进程。

---

## `/remove-machine`

从配置中移除一台远程机器。

**用法：**

```
/remove-machine <machine_id>
```

如果该机器上存在活跃或已分离的会话，系统会要求确认。机器条目会从 `config.yaml` 中删除。

---

## `/update`

拉取最新代码并重启 Head Node。仅管理员可用。

**用法：**

```
/update
```

在项目目录运行 `git pull`，然后替换正在运行的进程。需要在配置的 `admin_users` 中包含你的用户 ID。

---

## `/restart`

不拉取新代码直接重启 Head Node。仅管理员可用。

**用法：**

```
/restart
```

适用于加载配置变更或从异常状态中恢复。需要在配置的 `admin_users` 中包含你的用户 ID。

---

## `/help`

显示可用命令列表。

**用法：**

```
/help
```

---

## 发送消息

启动或恢复会话后，在频道中发送的任何非命令消息都会被转发给 AI。如果你输入的内容以 `/` 开头但不是已知的机器人命令，也会直接转发给 AI——这对于向 AI 命令行工具本身传递斜杠命令很有用。

响应实时流式回传。AI 处理期间会显示光标指示器或计时器。在 Discord 上，"机器人正在输入..."指示器和定期状态更新会在长时间操作期间让你了解进度。

如果在 AI 尚未完成上一条消息时发送新消息，新消息会自动排队并按顺序处理。

## 交互式问答（AskUserQuestion）

当 AI 使用 `AskUserQuestion` 工具时，Codecast 会以交互控件而非纯文本的形式呈现问题：

- **Discord** -- 消息下方的按钮，点击选择。
- **Telegram** -- 内联键盘，点击选择。
- **飞书** -- 交互卡片，点击选择。

对于多选题，每个选项会显示为单独的按钮或键。你的选择会作为回复发送给 AI。

## 文件转发

当 AI 响应中包含与已配置转发规则匹配的文件路径时，Codecast 可以自动从远程机器下载该文件并发送到聊天中，无需任何手动命令。

文件转发通过 `config.yaml` 中的 `file_forward` 配置。详情请参阅[配置指南](./configuration.md)。

## 平台差异

| 功能 | Discord | Telegram | 飞书 |
|---|---|---|---|
| 命令方式 | 带弹窗的斜杠命令 | 文本命令 | 文本命令 |
| 自动补全 | 机器 ID、路径、模式 | 不可用 | 不可用 |
| 消息长度限制 | 2000 字符 | 4096 字符 | 平台限制 |
| 交互式问答 | 按钮 | 内联键盘 | 交互卡片 |
| 访问控制 | 频道白名单 | 用户 ID 或会话白名单 | 会话 ID 白名单 |
| 管理员命令 | `admin_users` 中的用户 ID | `admin_users` 中的用户 ID | `admin_users` 中的用户 ID |
