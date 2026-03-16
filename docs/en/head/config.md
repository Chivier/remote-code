# Config Loader (config.py)

**File:** `head/config.py`

Handles loading, parsing, and validating the `config.yaml` configuration file. Defines all configuration dataclasses and provides environment variable expansion.

## Purpose

- Define typed configuration structure using Python dataclasses
- Load and parse YAML configuration files
- Expand `${ENV_VAR}` references in string values
- Expand `~` in file paths

## Dataclasses

### MachineConfig

Represents a single remote machine.

```python
@dataclass
class MachineConfig:
    id: str                              # Machine identifier (key from YAML)
    host: str                            # Hostname or IP
    user: str                            # SSH username
    ssh_key: Optional[str] = None        # Path to SSH private key
    port: int = 22                       # SSH port
    proxy_jump: Optional[str] = None     # Jump host machine ID
    proxy_command: Optional[str] = None  # SSH ProxyCommand string
    password: Optional[str] = None       # Password or "file:/path"
    daemon_port: int = 9100              # Remote daemon port
    node_path: Optional[str] = None      # Path to Node.js on remote
    default_paths: list[str] = []        # Common project paths
```

### DiscordConfig

```python
@dataclass
class DiscordConfig:
    token: str                           # Bot token
    allowed_channels: list[int] = []     # Channel ID whitelist (empty = all)
    command_prefix: str = "/"            # Command prefix
```

### TelegramConfig

```python
@dataclass
class TelegramConfig:
    token: str                           # Bot token
    allowed_users: list[int] = []        # User ID whitelist (empty = all)
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
    shared_dir: str = "./skills"         # Local skills directory
    sync_on_start: bool = True           # Sync on session creation
```

### DaemonDeployConfig

```python
@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.remote-code/daemon"   # Remote install path
    auto_deploy: bool = True                        # Auto-deploy daemon
    log_file: str = "~/.remote-code/daemon.log"  # Remote log file
```

### Config

Top-level configuration container:

```python
@dataclass
class Config:
    machines: dict[str, MachineConfig] = {}
    bot: BotConfig = BotConfig()
    default_mode: str = "auto"
    skills: SkillsConfig = SkillsConfig()
    daemon: DaemonDeployConfig = DaemonDeployConfig()
```

## Key Functions

### `load_config(config_path: str) -> Config`

Main entry point for configuration loading.

1. Reads the YAML file
2. Recursively expands `${ENV_VAR}` references through `_process_value()`
3. Parses `machines` section into `MachineConfig` objects (using the YAML key as the machine `id`)
4. Parses `bot.discord` and `bot.telegram` sections
5. Parses `default_mode`, `skills`, and `daemon` sections

Raises:
- `FileNotFoundError` if the config file does not exist
- `ValueError` if the config file is empty

### `expand_env_vars(value: str) -> str`

Replaces `${VARIABLE_NAME}` patterns with the corresponding environment variable value. If the variable is not set, the original `${...}` expression is left unchanged.

```python
# Example:
expand_env_vars("token: ${DISCORD_TOKEN}")
# → "token: my-actual-token"  (if DISCORD_TOKEN is set)
# → "token: ${DISCORD_TOKEN}" (if DISCORD_TOKEN is not set)
```

### `expand_path(path: str) -> str`

Combines environment variable expansion with `~` (home directory) expansion. Used for file paths like `ssh_key`.

```python
expand_path("~/.ssh/id_rsa")
# → "/home/user/.ssh/id_rsa"
```

### `_process_value(value: Any) -> Any`

Recursively processes all values in the config dictionary, expanding environment variables in strings and recursing into dicts and lists. Non-string, non-container values are returned unchanged.

## Connection to Other Modules

- **main.py** calls `load_config()` at startup
- **SSHManager** receives the full `Config` object and reads `MachineConfig` instances for SSH connections and `DaemonDeployConfig` for deployment settings
- **Bot classes** receive `Config` to access bot tokens and settings
