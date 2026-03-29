# 守护进程客户端（daemon_client.py）

**文件：** `head/daemon_client.py`

通过 SSH 隧道与远程代理守护进程通信的 JSON-RPC 客户端。同时处理常规 JSON 响应和 SSE（Server-Sent Events）流式响应。

## 用途

- 向守护进程的 HTTP 端点发送 JSON-RPC 请求
- 解析 `session.send` 的 SSE 流（Claude 的流式响应）
- 为每个 RPC 操作提供有类型的方法
- 处理连接错误和守护进程报告的错误

## 类：DaemonClient

```python
class DaemonClient:
    timeout: int = 300  # 默认超时时间（秒）
```

### 内部方法

#### `_url(local_port: int) -> str`

构建 RPC 端点 URL：`http://127.0.0.1:{local_port}/rpc`

#### `_rpc_call(local_port, method, params) -> dict`

使用给定方法和参数进行 JSON-RPC 调用。非流式调用使用 30 秒超时。如果响应包含错误，抛出 `DaemonError`；如果 HTTP 请求失败，抛出 `DaemonConnectionError`。

### 会话管理方法

#### `create_session(local_port, path, mode) -> str`

在远程机器上创建新的 Claude 会话。

- **参数：** `path`（项目目录）、`mode`（权限模式）
- **返回：** `sessionId`（UUID 字符串）

#### `send_message(local_port, session_id, message, idle_timeout) -> AsyncIterator[dict]`

向 Claude 会话发送消息，通过 SSE 流式回传事件。

这是与 Claude 交互的核心方法。它：

1. 发送 `session.send` JSON-RPC 请求
2. 将响应读取为 SSE 流（`text/event-stream`）
3. 将每行 `data: {...}` 解析为 JSON
4. 向调用方逐个产出解析后的事件字典
5. 收到 `data: [DONE]` 时返回

**超时行为：**
- 总超时：15 分钟（900 秒）
- 空闲超时（每次读取）：可配置，默认 300 秒（5 分钟）
- 如果在空闲超时内未收到任何事件，产出一个错误事件

**错误处理：**
- `asyncio.TimeoutError` -> 产出有关流空闲超时的错误事件
- `aiohttp.ClientError` -> 产出连接错误事件

#### `resume_session(local_port, session_id, sdk_session_id) -> dict`

恢复之前分离的会话。如果提供了 `sdk_session_id`，将传递给守护进程，用于未来 Claude 调用的 `--resume`。

返回包含 `ok`（bool）和 `fallback`（bool，表示是否创建了注入历史的新会话）的字典。

#### `destroy_session(local_port, session_id) -> bool`

销毁会话并终止任何正在运行的 Claude 进程。成功时返回 `True`。

#### `list_sessions(local_port) -> list[dict]`

列出远程守护进程上的所有会话。返回会话信息字典列表。

#### `set_mode(local_port, session_id, mode) -> bool`

设置会话的权限模式。成功时返回 `True`。

#### `interrupt_session(local_port, session_id) -> dict`

通过向 Claude CLI 进程发送 SIGTERM 来中断会话的当前 Claude 操作。返回包含以下字段的字典：
- `ok`（bool）：如果会话存在，始终为 `True`
- `interrupted`（bool）：如果有活跃操作被中断则为 `True`

#### `health_check(local_port) -> dict`

检查守护进程健康状态。返回会话数量、运行时间、内存使用情况、Node.js 版本和 PID。

#### `monitor_sessions(local_port) -> dict`

获取所有会话的详细监控信息，包括队列统计。

#### `reconnect_session(local_port, session_id) -> list[dict]`

重新连接到会话，并检索在客户端断开期间生成的任何缓冲事件。

#### `get_queue_stats(local_port, session_id) -> dict`

获取会话的消息队列统计信息：待处理的用户消息、待处理的响应以及客户端连接状态。

### 清理

#### `close() -> None`

关闭底层的 aiohttp 会话。在 Head Node 关闭时调用。

## 异常类

### `DaemonError`

当守护进程在 JSON-RPC 结果中返回错误响应时抛出。

```python
class DaemonError(Exception):
    code: int  # 来自守护进程的错误码
```

### `DaemonConnectionError`

当到守护进程的 HTTP 连接失败（网络错误、连接被拒绝等）时抛出。

## 与其他模块的关系

- **main.py** 创建 DaemonClient，并在关闭时调用 `close()`
- **BotBase** 响应用户命令和消息转发时调用所有会话管理方法
- **SSHManager** 提供映射到远程守护进程（通过 SSH 隧道）的 `local_port`
