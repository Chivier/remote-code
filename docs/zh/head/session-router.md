# 会话路由器（session_router.py）

**文件：** `head/session_router.py`

在本地 SQLite 数据库中管理会话状态。将机器人频道（Discord 或 Telegram）映射到远程机器上的活跃 Claude 会话。

## 用途

- 在 Head Node 重启后维持持久化的会话注册表
- 将聊天频道映射到远程 Claude 会话
- 追踪会话生命周期：active -> detached -> destroyed
- 记录会话历史以支持恢复功能
- 提供按频道、守护进程 ID 或机器/路径查找会话的方法

## 数据库模式

### `sessions` 表

存储每个会话的当前状态。主键是 `channel_id`（每个频道只有一个活跃会话）。

| 列 | 类型 | 说明 |
|---|---|---|
| `channel_id` | TEXT（PK） | 机器人特定的频道 ID（如 `discord:12345` 或 `telegram:67890`） |
| `machine_id` | TEXT | 远程机器标识符 |
| `path` | TEXT | 远程机器上的项目路径 |
| `daemon_session_id` | TEXT | 守护进程分配的 UUID |
| `sdk_session_id` | TEXT | Claude SDK 会话 ID（用于 `--resume`） |
| `status` | TEXT | `active`、`detached` 或 `destroyed` |
| `mode` | TEXT | 权限模式（`auto`、`code`、`plan`、`ask`） |
| `created_at` | TEXT | ISO 8601 时间戳 |
| `updated_at` | TEXT | ISO 8601 时间戳 |

### `session_log` 表

已分离会话的只追加日志。用于会话恢复查找。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER（PK） | 自增 ID |
| `channel_id` | TEXT | 原始频道 |
| `machine_id` | TEXT | 会话运行所在的机器 |
| `path` | TEXT | 项目路径 |
| `daemon_session_id` | TEXT | 守护进程会话 UUID |
| `sdk_session_id` | TEXT | Claude SDK 会话 ID |
| `mode` | TEXT | 分离时的权限模式 |
| `created_at` | TEXT | 会话创建时间 |
| `detached_at` | TEXT | 会话分离时间 |

在 `machine_id` 和 `daemon_session_id` 上建有索引，用于快速查找。

## Session 数据类

```python
@dataclass
class Session:
    channel_id: str           # 如 "discord:123456"
    machine_id: str           # 如 "gpu-1"
    path: str                 # 如 "/home/user/project"
    daemon_session_id: str    # 来自守护进程的 UUID
    sdk_session_id: Optional[str]  # Claude SDK 会话 ID
    status: str               # "active" | "detached" | "destroyed"
    mode: str                 # "auto" | "code" | "plan" | "ask"
    created_at: str           # ISO 8601
    updated_at: str           # ISO 8601
```

## 关键方法

### `resolve(channel_id: str) -> Optional[Session]`

查找频道的活跃会话。如果没有活跃会话，返回 `None`。这是在向 Claude 转发用户消息时使用的主要查找方法。

### `register(channel_id, machine_id, path, daemon_session_id, mode) -> None`

为频道注册新的活跃会话。如果该频道上已存在活跃会话，会先自动分离（移入会话日志）。新会话以 `active` 状态插入。

### `update_sdk_session(channel_id: str, sdk_session_id: str) -> None`

更新活跃会话的 SDK 会话 ID。当收到来自 Claude 的 `result` 事件时调用，该事件包含后续 `--resume` 调用所需的会话 ID。

### `update_mode(channel_id: str, mode: str) -> None`

更新频道上活跃会话的权限模式。当用户通过 `/mode` 更改模式时调用。

### `detach(channel_id: str) -> Optional[Session]`

在不销毁会话的情况下分离频道上的活跃会话。会话将：

1. 以当前时间戳作为 `detached_at` 复制到 `session_log`
2. 在 `sessions` 表中状态更新为 `detached`

返回已分离的会话，如果未找到活跃会话则返回 `None`。已分离的会话可以稍后用 `/resume` 恢复。

### `destroy(channel_id: str) -> Optional[Session]`

将会话标记为 `destroyed`。与 detach 不同，此操作不记录会话日志。返回已销毁的会话或 `None`。

### `list_sessions(machine_id: Optional[str]) -> list[Session]`

列出所有会话，可选按机器 ID 过滤。按 `updated_at` 降序（最近优先）返回。包含所有状态的会话。

### `list_active_sessions() -> list[Session]`

仅列出状态为 `active` 的会话。

### `find_session_by_daemon_id(daemon_session_id: str) -> Optional[Session]`

按守护进程分配的 UUID 查找会话。同时搜索活跃的 `sessions` 表和 `session_log` 表。由 `/resume` 命令使用，用于定位之前分离的会话。

### `find_sessions_by_machine_path(machine_id: str, path: str) -> list[Session]`

查找特定机器和路径上的所有会话。由 `/rm` 命令使用，销毁匹配机器/路径组合的会话。

## 与其他模块的关系

- **main.py** 使用数据库路径创建 SessionRouter
- **BotBase** 在每次消息转发前调用 `resolve()`，在 `/start` 时调用 `register()`，在 `/exit` 时调用 `detach()`，通过 `/rm` 调用 `destroy()`，以及为 `/ls`、`/resume`、`/status` 调用查询方法
- **BotBase** 在 `result` 事件提供 Claude SDK 会话 ID 时调用 `update_sdk_session()`
- **BotBase** 在用户更改权限模式时调用 `update_mode()`
