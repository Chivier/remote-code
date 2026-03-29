# SSH 管理器（ssh_manager.py）

**文件：** `head/ssh_manager.py`

管理 SSH 连接、端口转发隧道、远程守护进程部署和技能同步。这是本地 Head Node 与远程机器之间的桥梁。

## 用途

- 维护到远程机器的 SSH 连接和隧道池
- 创建本地端口转发隧道以访问远程守护进程
- 通过 SCP 将守护进程代码部署到远程机器
- 在远程机器上启动守护进程并检查其健康状态
- 将技能文件同步到远程项目目录
- 列出机器及其在线/守护进程状态

## 类

### SSHTunnel

表示到远程机器的活跃 SSH 隧道。

```python
class SSHTunnel:
    machine_id: str          # 该隧道连接的机器
    local_port: int          # 本地端口（如 19100）
    conn: SSHClientConnection  # asyncssh 连接
    listener: SSHListener    # 端口转发监听器
```

**属性：**
- `alive` -- 如果底层 SSH 连接仍然打开，返回 `True`。

**方法：**
- `close()` -- 关闭端口转发监听器和 SSH 连接。

### SSHManager

管理所有 SSH 操作的主类。

```python
class SSHManager:
    config: Config
    machines: dict[str, MachineConfig]
    tunnels: dict[str, SSHTunnel]      # machine_id -> 活跃隧道
```

## 关键方法

### `ensure_tunnel(machine_id: str) -> int`

确保到指定机器的 SSH 隧道存在。返回访问守护进程所需的本地端口号。

**流程：**
1. 检查隧道是否已存在且存活——如果是，返回现有本地端口
2. 如果隧道已死，关闭并移除
3. 分配新本地端口（从 19100 开始递增）
4. 通过 `_connect_ssh()` 建立 SSH 连接
5. 创建本地端口转发：`127.0.0.1:<local_port>` -> `127.0.0.1:<daemon_port>`
6. 通过 `_ensure_daemon()` 确保守护进程在远程机器上运行
7. 存储隧道并返回本地端口

### `_connect_ssh(machine: MachineConfig) -> SSHClientConnection`

建立到一台机器的 SSH 连接。处理以下情况：

- **SSH 密钥认证**：如果配置了 `ssh_key`，使用 `client_keys`
- **密码认证**：支持直接密码和 `file:/path` 语法
- **ProxyJump**：通过先建立到跳板机的连接，再将其用作最终连接的 `tunnel` 参数来实现跳转
- **known_hosts**：禁用（`known_hosts=None`），适用于可信环境的简化方案

### `_ensure_daemon(machine_id: str, conn: SSHClientConnection) -> None`

确保守护进程在远程机器上运行。

**流程：**
1. 通过 `pgrep` 检查 `node.*dist/server.js` 进程是否已在运行
2. 如果正在运行，立即返回
3. 检查守护进程代码是否存在于 `install_dir`（`dist/server.js` 和 `node_modules/` 均需存在）
4. 如果缺失且 `auto_deploy` 已启用，调用 `_deploy_daemon()`
5. 用 `nohup` 启动守护进程，设置：
   - `DAEMON_PORT` 环境变量
   - PATH 包含 Node.js 二进制目录和 `~/.local/bin`（用于 Claude CLI）
6. 每 2 秒轮询健康检查端点，最多等待 30 秒
7. 如果守护进程未在超时时间内响应，抛出 `RuntimeError`

### `_deploy_daemon(machine_id: str, conn: SSHClientConnection) -> None`

通过 SCP 将守护进程代码部署到远程机器。

**流程：**
1. 如果 `daemon/dist/` 不存在，在本地构建守护进程（`npm run build`）
2. 在远程创建安装目录
3. 将 `package.json` 和 `package-lock.json` SCP 到远程
4. 递归 SCP 整个 `dist/` 目录
5. 在远程机器上运行 `npm install --production`
6. 如果 npm 在非标准位置，从 `node_path` 推导其路径

### `sync_skills(machine_id: str, remote_path: str) -> None`

将技能文件从本地 `skills.shared_dir` 同步到远程项目路径。

**行为：**
- 如果 `skills.sync_on_start` 为 `false`，完全跳过
- 将 `CLAUDE.md` 复制到远程项目根目录，但仅在远程该位置不存在时才复制（不覆盖已有文件）
- 递归将 `.claude/skills/` 目录复制到远程项目
- 如果已有 SSH 隧道连接，使用现有连接；否则创建新连接
- 错误以警告级别记录，不影响会话创建

### `list_machines() -> list[dict]`

列出所有已配置机器及其在线和守护进程状态。

**行为：**
- 跳过仅作为跳板机使用的机器（被 `proxy_jump` 引用且没有 `default_paths`）
- 对每台机器，尝试 15 秒超时的 SSH 连接
- 如果可达，通过 `pgrep` 检查守护进程是否运行
- 返回包含 `id`、`host`、`user`、`status`（online/offline）、`daemon`（running/stopped/unknown）、`default_paths` 的字典列表

### `get_local_port(machine_id: str) -> Optional[int]`

如果存在活跃隧道，返回该机器的本地隧道端口；否则返回 `None`。

### `close_all() -> None`

关闭所有 SSH 隧道和连接。在优雅关闭时调用。

## 端口分配

SSH 隧道的本地端口从 `19100` 开始顺序分配：

```
gpu-1 -> localhost:19100
gpu-2 -> localhost:19101
gpu-3 -> localhost:19102
...
```

这种简单的分配方式之所以有效，是因为 Head Node 在单一进程中管理所有隧道。

## 与其他模块的关系

- **main.py** 使用完整配置创建 SSHManager，并在关闭时调用 `close_all()`
- **BotBase** 在每次守护进程 RPC 调用前调用 `ensure_tunnel()`，在 `/start` 时调用 `sync_skills()`
- **BotBase** 为 `/ls machine` 命令调用 `list_machines()`
- **BotBase** 在检查所有已连接机器时，为 `/health` 命令调用 `get_local_port()`
