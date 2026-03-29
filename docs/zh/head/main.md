# 入口点（main.py）

**文件：** `head/main.py`

Codecast Head Node 的入口点。该模块通过加载配置、初始化共享组件、启动机器人和处理优雅关闭来引导整个系统。

## 用途

- 加载并验证 `config.yaml`
- 创建共享基础设施（SSHManager、SessionRouter、DaemonClient）
- 初始化并启动 Discord 和/或 Telegram 机器人
- 处理 SIGTERM/SIGINT 的优雅关闭

## 主函数

```python
async def main(config_path: str = "config.yaml") -> None
```

`main()` 协程是主入口点，执行以下步骤：

### 1. 加载配置

```python
config = load_config(config_path)
```

加载 `config.yaml`（或通过命令行参数传入的自定义路径）。如果文件不存在或无效，进程会以错误消息退出。

### 2. 初始化共享组件

```python
ssh_manager = SSHManager(config)
session_router = SessionRouter(db_path=str(Path(__file__).parent / "sessions.db"))
daemon_client = DaemonClient()
```

这三个组件只创建一次，在所有机器人之间共享：

- **SSHManager**：管理到所有已配置机器的 SSH 连接和隧道。接受完整的配置以访问机器定义和守护进程部署设置。
- **SessionRouter**：基于 SQLite 的会话注册表。数据库存储为 `head/sessions.db`（位于 Python 源文件旁边）。
- **DaemonClient**：无状态的 JSON-RPC 客户端，使用共享的 aiohttp 会话。

### 3. 初始化机器人

```python
discord_bot = DiscordBot(ssh_manager, session_router, daemon_client, config)
telegram_bot = TelegramBot(ssh_manager, session_router, daemon_client, config)
```

只有在配置了 token 的情况下才会创建对应的机器人。如果没有任何机器人配置有效 token，进程会以错误退出。

### 4. 启动机器人

```python
task = asyncio.create_task(discord_bot.start(), name="discord")
task = asyncio.create_task(telegram_bot.start(), name="telegram")
```

机器人作为并发的 asyncio 任务运行。主协程随后等待以下任一情况：
- 关闭信号（SIGTERM/SIGINT）
- 某个机器人任务崩溃（最先完成者）

### 5. 优雅关闭

```python
def handle_shutdown(sig: signal.Signals) -> None:
    shutdown_event.set()
```

`SIGTERM` 和 `SIGINT` 的信号处理器会设置一个关闭事件。触发时：

1. 所有机器人通过 `bot.stop()` 停止
2. DaemonClient 的 HTTP 会话关闭
3. 所有 SSH 隧道通过 `ssh_manager.close_all()` 关闭
4. 剩余的 asyncio 任务被取消

## 命令行用法

```bash
# 默认配置
python -m head.main

# 自定义配置路径
python -m head.main /path/to/config.yaml
```

配置路径从 `sys.argv[1]` 读取（如果提供），默认为 `"config.yaml"`。

## 日志记录

该模块以 `INFO` 级别配置 Python 的日志系统，格式为：

```
2026-03-14 10:00:00 [codecast] INFO: message
```

`head/` 下的所有模块使用 `logging.getLogger(__name__)` 并继承此配置。

## 错误处理

- 配置文件缺失：记录错误并以代码 1 退出
- machines 字典为空：记录错误并以代码 1 退出
- 没有配置机器人（没有 token）：记录错误并以代码 1 退出
- 运行时机器人崩溃：记录异常，触发关闭
- 清理错误：以警告级别记录，不影响其他清理步骤
