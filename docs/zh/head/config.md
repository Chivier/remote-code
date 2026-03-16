# 配置加载 (config.py)

`config.py` 负责定义配置数据结构、加载 YAML 配置文件以及展开环境变量。

**源文件**：`head/config.py`

## 职责

1. 定义所有配置相关的数据类（dataclass）
2. 加载和解析 `config.yaml` 文件
3. 递归展开配置值中的 `${ENV_VAR}` 引用
4. 展开路径中的 `~` 为用户主目录

## 数据类

### MachineConfig

表示一台远程机器的配置。

```python
@dataclass
class MachineConfig:
    id: str                              # 机器唯一标识
    host: str                            # 主机名或 IP
    user: str                            # SSH 用户名
    ssh_key: Optional[str] = None        # SSH 私钥路径
    port: int = 22                       # SSH 端口
    proxy_jump: Optional[str] = None     # 跳板机 ID
    proxy_command: Optional[str] = None  # SSH ProxyCommand
    password: Optional[str] = None       # SSH 密码（支持 file: 前缀）
    daemon_port: int = 9100              # Daemon RPC 端口
    node_path: Optional[str] = None      # Node.js 路径
    default_paths: list[str] = []        # 常用项目路径
```

### DiscordConfig

```python
@dataclass
class DiscordConfig:
    token: str                                   # Bot Token
    allowed_channels: list[int] = []             # 允许的频道 ID
    command_prefix: str = "/"                    # 命令前缀
```

### TelegramConfig

```python
@dataclass
class TelegramConfig:
    token: str                              # Bot Token
    allowed_users: list[int] = []           # 允许的用户 ID
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
    shared_dir: str = "./skills"      # 本地技能目录
    sync_on_start: bool = True        # 启动时是否同步
```

### DaemonDeployConfig

```python
@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.remote-code/daemon"   # 远程安装目录
    auto_deploy: bool = True                       # 自动部署
    log_file: str = "~/.remote-code/daemon.log"  # 远程日志文件
```

### Config

顶层配置对象，包含所有子配置。

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

### expand_env_vars(value: str) -> str

展开字符串中的 `${ENV_VAR}` 引用。使用正则表达式 `\$\{(\w+)\}` 匹配环境变量名，并从 `os.environ` 中查找替换值。

如果环境变量未定义，保持原始的 `${VAR}` 文本不变。

```python
# 示例
expand_env_vars("token-${DISCORD_TOKEN}-end")
# 如果 DISCORD_TOKEN=abc123，结果为 "token-abc123-end"
# 如果未定义，结果为 "token-${DISCORD_TOKEN}-end"
```

### expand_path(path: str) -> str

组合 `expand_env_vars` 和 `Path.expanduser()` 来完整展开路径：

```python
expand_path("~/.ssh/${KEY_NAME}")
# → "/home/user/.ssh/my_key"
```

### _process_value(value: Any) -> Any

递归处理配置值。对字符串调用 `expand_env_vars`，对字典和列表递归处理内部元素，其他类型原样返回。

### load_config(config_path: str) -> Config

主加载函数。流程：

1. 检查文件是否存在，不存在抛出 `FileNotFoundError`
2. 使用 `yaml.safe_load()` 解析 YAML
3. 使用 `_process_value()` 递归展开所有环境变量
4. 手动解析每个配置段（machines、bot、skills、daemon）
5. 返回填充完整的 `Config` 对象

#### 解析细节

**machines 解析**：
- 每台机器的 ID 就是 YAML 中的键名
- `host` 默认为机器 ID
- `user` 默认为当前系统用户 (`os.environ.get("USER", "root")`)
- `ssh_key` 路径会通过 `expand_path()` 展开
- `allowed_channels` 和 `allowed_users` 中的值会被转换为 `int` 类型

**bot 解析**：
- 只在存在对应的配置段且 token 不为空时才创建 Bot 配置

**default_mode**：
- 默认为 `"auto"`

## 与其他模块的关系

`config.py` 是基础模块，被几乎所有其他模块依赖：

- `main.py` — 调用 `load_config()` 加载配置
- `ssh_manager.py` — 使用 `Config` 和 `MachineConfig` 管理连接
- `bot_discord.py` — 使用 `DiscordConfig` 配置 Bot
- `bot_telegram.py` — 使用 `TelegramConfig` 配置 Bot
- `bot_base.py` — 使用 `Config` 读取 `default_mode` 等全局设置
