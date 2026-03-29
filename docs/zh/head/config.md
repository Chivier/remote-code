# 配置加载器（config.py）

**文件：** `head/config.py`

负责加载、解析和验证 `config.yaml` 配置文件。定义所有配置数据类并提供环境变量展开功能。

## 用途

- 使用 Python 数据类定义有类型的配置结构
- 加载并解析 YAML 配置文件
- 展开字符串值中的 `${ENV_VAR}` 引用
- 展开文件路径中的 `~`

## 数据类

### MachineConfig

表示单台远程机器。

```python
@dataclass
class MachineConfig:
    id: str                              # 机器标识符（来自 YAML 的键）
    host: str                            # 主机名或 IP
    user: str                            # SSH 用户名
    ssh_key: Optional[str] = None        # SSH 私钥路径
    port: int = 22                       # SSH 端口
    proxy_jump: Optional[str] = None     # 跳板机的机器 ID
    proxy_command: Optional[str] = None  # SSH ProxyCommand 字符串
    password: Optional[str] = None       # 密码或 "file:/path"
    daemon_port: int = 9100              # 远程守护进程端口
    node_path: Optional[str] = None      # 远程 Node.js 路径
    default_paths: list[str] = []        # 常用项目路径
```

### DiscordConfig

```python
@dataclass
class DiscordConfig:
    token: str                           # 机器人 token
    allowed_channels: list[int] = []     # 频道 ID 白名单（为空 = 所有）
    command_prefix: str = "/"            # 命令前缀
```

### TelegramConfig

```python
@dataclass
class TelegramConfig:
    token: str                           # 机器人 token
    allowed_users: list[int] = []        # 用户 ID 白名单（为空 = 所有）
```

### BotConfig

```python
@dataclass
class BotConfig:
    discord: Optional[DiscordConfig] = None
    telegram: Optional[TelegramConfig] = None
```

### SkillsConfig

```python
@dataclass
class SkillsConfig:
    shared_dir: str = "./skills"         # 本地技能目录
    sync_on_start: bool = True           # 会话创建时同步
```

### DaemonDeployConfig

```python
@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.codecast/daemon"   # 远程安装路径
    auto_deploy: bool = True                        # 自动部署守护进程
    log_file: str = "~/.codecast/daemon.log"  # 远程日志文件
```

### Config

顶层配置容器：

```python
@dataclass
class Config:
    machines: dict[str, MachineConfig] = {}
    bot: BotConfig = BotConfig()
    default_mode: str = "auto"
    skills: SkillsConfig = SkillsConfig()
    daemon: DaemonDeployConfig = DaemonDeployConfig()
```

## 关键函数

### `load_config(config_path: str) -> Config`

配置加载的主入口。

1. 读取 YAML 文件
2. 通过 `_process_value()` 递归展开 `${ENV_VAR}` 引用
3. 将 `machines` 部分解析为 `MachineConfig` 对象（使用 YAML 键作为机器的 `id`）
4. 解析 `bot.discord` 和 `bot.telegram` 部分
5. 解析 `default_mode`、`skills` 和 `daemon` 部分

抛出：
- 如果配置文件不存在，抛出 `FileNotFoundError`
- 如果配置文件为空，抛出 `ValueError`

### `expand_env_vars(value: str) -> str`

将 `${VARIABLE_NAME}` 模式替换为对应的环境变量值。如果变量未设置，原始的 `${...}` 表达式保持不变。

```python
# 示例：
expand_env_vars("token: ${DISCORD_TOKEN}")
# → "token: my-actual-token"（如果 DISCORD_TOKEN 已设置）
# → "token: ${DISCORD_TOKEN}"（如果 DISCORD_TOKEN 未设置）
```

### `expand_path(path: str) -> str`

将环境变量展开与 `~`（主目录）展开结合。用于 `ssh_key` 等文件路径。

```python
expand_path("~/.ssh/id_rsa")
# → "/home/user/.ssh/id_rsa"
```

### `_process_value(value: Any) -> Any`

递归处理配置字典中的所有值，对字符串展开环境变量，对字典和列表递归处理。非字符串、非容器类型的值原样返回。

## 与其他模块的关系

- **main.py** 在启动时调用 `load_config()`
- **SSHManager** 接收完整的 `Config` 对象，读取 `MachineConfig` 实例用于 SSH 连接，读取 `DaemonDeployConfig` 用于部署设置
- **各机器人类** 接收 `Config` 以访问机器人 token 和相关设置
