# 主入口 (main.py)

`main.py` 是 Remote Code Head Node 的入口点，负责加载配置、初始化所有核心组件、启动 Bot 并处理优雅关闭。

**源文件**：`head/main.py`

## 职责

1. 加载并验证 `config.yaml` 配置文件
2. 实例化三个核心组件：`SSHManager`、`SessionRouter`、`DaemonClient`
3. 根据配置创建 Discord Bot 和/或 Telegram Bot
4. 并发启动所有 Bot
5. 监听系统信号（SIGTERM/SIGINT）进行优雅关闭
6. 清理所有资源（Bot、HTTP 客户端、SSH 隧道）

## 启动流程

```python
async def main(config_path: str = "config.yaml") -> None:
```

### 1. 配置加载

```python
config = load_config(config_path)
```

从指定路径加载 YAML 配置。如果文件不存在或解析失败，程序直接退出并打印错误信息。

### 2. 组件初始化

```python
ssh_manager = SSHManager(config)
session_router = SessionRouter(db_path=str(Path(__file__).parent / "sessions.db"))
daemon_client = DaemonClient()
```

- `SSHManager` — 接受完整的 `Config` 对象，管理到所有远程机器的 SSH 连接
- `SessionRouter` — 使用 SQLite 数据库（位于 `head/sessions.db`）持久化会话状态
- `DaemonClient` — 无状态的 JSON-RPC 客户端，按需创建 HTTP 会话

### 3. Bot 初始化

```python
discord_bot = DiscordBot(ssh_manager, session_router, daemon_client, config)
telegram_bot = TelegramBot(ssh_manager, session_router, daemon_client, config)
```

两种 Bot 都接收相同的三个共享组件和配置对象。只有在 `config.yaml` 中配置了对应的 token 时才会创建对应的 Bot。

如果没有配置任何 Bot（既没有 Discord token 也没有 Telegram token），程序会报错退出。

### 4. 信号处理

```python
def handle_shutdown(sig: signal.Signals) -> None:
    logger.info(f"Received {sig.name}, shutting down...")
    shutdown_event.set()

for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, handle_shutdown, sig)
```

使用 `asyncio.Event` 和信号处理器实现优雅关闭。收到 SIGTERM 或 SIGINT 时设置事件，主循环检测到后开始清理。

### 5. 并发启动

```python
if discord_bot:
    task = asyncio.create_task(discord_bot.start(), name="discord")
if telegram_bot:
    task = asyncio.create_task(telegram_bot.start(), name="telegram")
```

每个 Bot 作为独立的 asyncio Task 运行。使用 `asyncio.wait()` 等待：
- shutdown_event 被设置（用户请求关闭）
- 任何一个 Bot task 完成（可能是崩溃）

如果某个 Bot 崩溃（task 有异常），会记录错误日志。

### 6. 清理流程

```python
# 停止所有 Bot
for bot in bots:
    await bot.stop()

# 关闭 HTTP 客户端
await daemon_client.close()

# 关闭所有 SSH 隧道
await ssh_manager.close_all()

# 取消残余任务
for task in tasks:
    if not task.done():
        task.cancel()
```

清理的顺序经过精心设计：
1. **先停止 Bot** — 不再接收新的用户消息
2. **关闭 HTTP 客户端** — 断开与 Daemon 的连接
3. **关闭 SSH 隧道** — 释放网络资源
4. **取消残余任务** — 确保没有泄漏的协程

## 命令行使用

```bash
# 使用默认配置文件 (config.yaml)
python -m head.main

# 使用指定配置文件
python -m head.main /path/to/my-config.yaml
```

## 日志

使用 Python 标准 `logging` 模块，配置格式为：

```
%(asctime)s [%(name)s] %(levelname)s: %(message)s
```

日志级别默认为 `INFO`。各模块的 logger 名称：
- `remote-code` — 主入口
- `head.ssh_manager` — SSH 管理
- `head.session_router` — 会话路由
- `head.daemon_client` — Daemon 客户端
- `head.bot_discord` / `head.bot_telegram` — Bot 模块

## 与其他模块的关系

`main.py` 是唯一直接实例化所有核心组件的模块：

```
main.py
  ├── load_config()     → config.py
  ├── SSHManager()      → ssh_manager.py
  ├── SessionRouter()   → session_router.py
  ├── DaemonClient()    → daemon_client.py
  ├── DiscordBot()      → bot_discord.py
  └── TelegramBot()     → bot_telegram.py
```

所有组件通过构造函数注入的方式共享，Bot 实例持有对 `ssh_manager`、`session_router` 和 `daemon_client` 的引用。
