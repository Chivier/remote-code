"""
Configuration loader for Remote Claude Head Node.
Reads config.yaml and expands environment variables.
"""

import os
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class MachineConfig:
    id: str
    host: str
    user: str
    ssh_key: Optional[str] = None
    port: int = 22
    proxy_jump: Optional[str] = None
    proxy_command: Optional[str] = None  # SSH ProxyCommand string
    password: Optional[str] = None  # SSH password (or path prefixed with 'file:')
    daemon_port: int = 9100
    node_path: Optional[str] = None
    default_paths: list[str] = field(default_factory=list)


@dataclass
class DiscordConfig:
    token: str
    allowed_channels: list[int] = field(default_factory=list)
    command_prefix: str = "/"


@dataclass
class TelegramConfig:
    token: str
    allowed_users: list[int] = field(default_factory=list)


@dataclass
class BotConfig:
    discord: Optional[DiscordConfig] = None
    telegram: Optional[TelegramConfig] = None


@dataclass
class SkillsConfig:
    shared_dir: str = "./skills"
    sync_on_start: bool = True


@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.remote-claude/daemon"
    auto_deploy: bool = True
    log_file: str = "~/.remote-claude/daemon.log"


DEFAULT_ALLOWED_FILE_TYPES = [
    "text/plain",
    "text/markdown",
    "application/pdf",
    "image/*",
    "video/*",
    "audio/*",
]


@dataclass
class FilePoolConfig:
    max_size: int = 1073741824  # 1GB in bytes
    pool_dir: str = "~/.remote-claude/file-pool"
    remote_dir: str = "/tmp/remote-claude/files"
    allowed_types: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_FILE_TYPES))


@dataclass
class Config:
    machines: dict[str, MachineConfig] = field(default_factory=dict)
    bot: BotConfig = field(default_factory=BotConfig)
    default_mode: str = "auto"
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    daemon: DaemonDeployConfig = field(default_factory=DaemonDeployConfig)
    file_pool: FilePoolConfig = field(default_factory=FilePoolConfig)
    tool_batch_size: int = 15  # Number of tool_use messages to batch into one


def expand_env_vars(value: str) -> str:
    """Expand ${ENV_VAR} references in a string."""
    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return re.sub(r'\$\{(\w+)\}', replacer, value)


def expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return str(Path(expand_env_vars(path)).expanduser())


def _process_value(value: Any) -> Any:
    """Recursively expand env vars in config values."""
    if isinstance(value, str):
        return expand_env_vars(value)
    elif isinstance(value, dict):
        return {k: _process_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_process_value(item) for item in value]
    return value


def load_config(config_path: str = "config.yaml") -> Config:
    """Load and parse the config.yaml file."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_file) as f:
        raw_data: dict[str, Any] = yaml.safe_load(f)

    if not raw_data:
        raise ValueError("Config file is empty")

    # Expand env vars throughout
    raw: dict[str, Any] = _process_value(raw_data)

    config = Config()

    # Parse machines
    machines_raw: dict[str, Any] = raw.get("machines", {})
    for machine_id, machine_data in machines_raw.items():
        md: dict[str, Any] = machine_data
        mc = MachineConfig(
            id=machine_id,
            host=md.get("host", machine_id),
            user=md.get("user", os.environ.get("USER", "root")),
            ssh_key=expand_path(md["ssh_key"]) if "ssh_key" in md else None,
            port=md.get("port", 22),
            proxy_jump=md.get("proxy_jump"),
            proxy_command=md.get("proxy_command"),
            password=md.get("password"),
            daemon_port=md.get("daemon_port", 9100),
            node_path=md.get("node_path"),
            default_paths=md.get("default_paths", []),
        )
        config.machines[machine_id] = mc

    # Parse bot config
    bot_raw: dict[str, Any] = raw.get("bot", {})
    if bot_raw:
        discord_raw: Optional[dict[str, Any]] = bot_raw.get("discord")
        if discord_raw:
            config.bot.discord = DiscordConfig(
                token=discord_raw.get("token", ""),
                allowed_channels=[int(c) for c in discord_raw.get("allowed_channels", [])],
                command_prefix=discord_raw.get("command_prefix", "/"),
            )
        telegram_raw: Optional[dict[str, Any]] = bot_raw.get("telegram")
        if telegram_raw:
            config.bot.telegram = TelegramConfig(
                token=telegram_raw.get("token", ""),
                allowed_users=[int(u) for u in telegram_raw.get("allowed_users", [])],
            )

    # Parse other config
    config.default_mode = raw.get("default_mode", "auto")
    config.tool_batch_size = int(raw.get("tool_batch_size", 15))

    skills_raw: dict[str, Any] = raw.get("skills", {})
    if skills_raw:
        config.skills = SkillsConfig(
            shared_dir=skills_raw.get("shared_dir", "./skills"),
            sync_on_start=skills_raw.get("sync_on_start", True),
        )

    daemon_raw: dict[str, Any] = raw.get("daemon", {})
    if daemon_raw:
        config.daemon = DaemonDeployConfig(
            install_dir=daemon_raw.get("install_dir", "~/.remote-claude/daemon"),
            auto_deploy=daemon_raw.get("auto_deploy", True),
            log_file=daemon_raw.get("log_file", "~/.remote-claude/daemon.log"),
        )

    file_pool_raw: dict[str, Any] = raw.get("file_pool", {})
    if file_pool_raw:
        config.file_pool = FilePoolConfig(
            max_size=file_pool_raw.get("max_size", 1073741824),
            pool_dir=expand_env_vars(file_pool_raw.get("pool_dir", "~/.remote-claude/file-pool")),
            remote_dir=file_pool_raw.get("remote_dir", "/tmp/remote-claude/files"),
            allowed_types=file_pool_raw.get("allowed_types", list(DEFAULT_ALLOWED_FILE_TYPES)),
        )

    return config
