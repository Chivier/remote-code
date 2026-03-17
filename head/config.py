"""
Configuration loader for Codecast Head Node.
Reads config.yaml and expands environment variables.
"""

import logging
import os
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


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
    localhost: bool = False  # True if this machine is the head node itself


@dataclass
class DiscordConfig:
    token: str
    allowed_channels: list[int] = field(default_factory=list)
    command_prefix: str = "/"
    admin_users: list[int] = field(default_factory=list)  # Discord user IDs for /restart, /update


@dataclass
class TelegramConfig:
    token: str
    allowed_users: list[int] = field(default_factory=list)
    admin_users: list[int] = field(default_factory=list)
    allowed_chats: list[int] = field(default_factory=list)


@dataclass
class LarkConfig:
    app_id: str
    app_secret: str
    allowed_chats: list[str] = field(default_factory=list)
    admin_users: list[str] = field(default_factory=list)
    use_cards: bool = True


@dataclass
class FileForwardRule:
    pattern: str
    max_size: int = 5 * 1024 * 1024
    auto: bool = False


@dataclass
class FileForwardConfig:
    enabled: bool = False
    rules: list[FileForwardRule] = field(default_factory=list)
    default_max_size: int = 5 * 1024 * 1024
    default_auto: bool = False
    download_dir: str = "~/.codecast/downloads"


@dataclass
class BotConfig:
    discord: Optional[DiscordConfig] = None
    telegram: Optional[TelegramConfig] = None
    lark: Optional[LarkConfig] = None


@dataclass
class SkillsConfig:
    shared_dir: str = "./skills"
    sync_on_start: bool = True


@dataclass
class DaemonDeployConfig:
    install_dir: str = "~/.codecast/daemon"
    auto_deploy: bool = True
    log_file: str = "~/.codecast/daemon.log"


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
    pool_dir: str = "~/.codecast/file-pool"
    remote_dir: str = "/tmp/codecast/files"
    allowed_types: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_FILE_TYPES))


@dataclass
class Config:
    machines: dict[str, MachineConfig] = field(default_factory=dict)
    bot: BotConfig = field(default_factory=BotConfig)
    default_mode: str = "auto"
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    daemon: DaemonDeployConfig = field(default_factory=DaemonDeployConfig)
    file_pool: FilePoolConfig = field(default_factory=FilePoolConfig)
    file_forward: FileForwardConfig = field(default_factory=FileForwardConfig)
    tool_batch_size: int = 15  # Number of tool_use messages to batch into one


def expand_env_vars(value: str) -> str:
    """Expand ${ENV_VAR} references in a string."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _is_localhost(host: str) -> bool:
    """Check if a host string refers to the local machine.

    Checks against: localhost, 127.0.0.1, ::1, current hostname,
    and all local network interface IPs.
    """
    host_lower = host.lower()
    if host_lower in ("localhost", "127.0.0.1", "::1"):
        return True

    import socket

    # Check hostname
    try:
        if host_lower == socket.gethostname().lower():
            return True
        if host_lower == socket.getfqdn().lower():
            return True
    except Exception:
        pass

    # Check all local IPs
    try:
        local_ips = set()
        for info in socket.getaddrinfo(socket.gethostname(), None):
            local_ips.add(info[4][0])
        # Also grab IPs from all interfaces via subprocess (more reliable)
        import subprocess

        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for ip in result.stdout.strip().split():
                local_ips.add(ip.strip())
        if host in local_ips:
            return True
    except Exception:
        pass

    return False


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
    config._config_path = str(config_file.resolve())  # type: ignore[attr-defined]

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
            localhost=md.get("localhost", _is_localhost(md.get("host", machine_id))),
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
                admin_users=[int(u) for u in discord_raw.get("admin_users", [])],
            )
        telegram_raw: Optional[dict[str, Any]] = bot_raw.get("telegram")
        if telegram_raw:
            config.bot.telegram = TelegramConfig(
                token=telegram_raw.get("token", ""),
                allowed_users=[int(u) for u in telegram_raw.get("allowed_users", [])],
                admin_users=[int(u) for u in telegram_raw.get("admin_users", [])],
                allowed_chats=[int(c) for c in telegram_raw.get("allowed_chats", [])],
            )
        lark_raw: Optional[dict[str, Any]] = bot_raw.get("lark")
        if lark_raw:
            config.bot.lark = LarkConfig(
                app_id=lark_raw.get("app_id", ""),
                app_secret=lark_raw.get("app_secret", ""),
                allowed_chats=[str(c) for c in lark_raw.get("allowed_chats", [])],
                admin_users=[str(u) for u in lark_raw.get("admin_users", [])],
                use_cards=lark_raw.get("use_cards", True),
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
            install_dir=daemon_raw.get("install_dir", "~/.codecast/daemon"),
            auto_deploy=daemon_raw.get("auto_deploy", True),
            log_file=daemon_raw.get("log_file", "~/.codecast/daemon.log"),
        )

    file_pool_raw: dict[str, Any] = raw.get("file_pool", {})
    if file_pool_raw:
        config.file_pool = FilePoolConfig(
            max_size=file_pool_raw.get("max_size", 1073741824),
            pool_dir=expand_env_vars(file_pool_raw.get("pool_dir", "~/.codecast/file-pool")),
            remote_dir=file_pool_raw.get("remote_dir", "/tmp/codecast/files"),
            allowed_types=file_pool_raw.get("allowed_types", list(DEFAULT_ALLOWED_FILE_TYPES)),
        )

    file_forward_raw: dict[str, Any] = raw.get("file_forward", {})
    if file_forward_raw:
        rules = []
        for rule_raw in file_forward_raw.get("rules", []):
            rules.append(
                FileForwardRule(
                    pattern=rule_raw.get("pattern", "*"),
                    max_size=rule_raw.get("max_size", 5 * 1024 * 1024),
                    auto=rule_raw.get("auto", False),
                )
            )
        config.file_forward = FileForwardConfig(
            enabled=file_forward_raw.get("enabled", False),
            rules=rules,
            default_max_size=file_forward_raw.get("default_max_size", 5 * 1024 * 1024),
            default_auto=file_forward_raw.get("default_auto", False),
            download_dir=file_forward_raw.get("download_dir", "~/.codecast/downloads"),
        )

    return config


# ─── Config Persistence (add/remove machines) ───


def _get_config_path(config: Config) -> Path:
    """Get the config file path. Falls back to 'config.yaml' in project root."""
    if hasattr(config, "_config_path"):
        return Path(config._config_path)  # type: ignore[attr-defined]
    return Path(__file__).parent.parent / "config.yaml"


def save_machine_to_config(config: Config, machine: MachineConfig) -> None:
    """
    Add or update a machine entry in config.yaml using ruamel.yaml
    to preserve comments and formatting.
    """
    config_path = _get_config_path(config)
    ryaml = YAML()
    ryaml.preserve_quotes = True  # type: ignore[assignment]

    with open(config_path) as f:
        doc = ryaml.load(f)

    if "machines" not in doc or doc["machines"] is None:
        doc["machines"] = {}

    # Build machine dict
    m: dict[str, Any] = {}
    m["host"] = machine.host
    m["user"] = machine.user
    if machine.ssh_key:
        m["ssh_key"] = machine.ssh_key
    if machine.port != 22:
        m["port"] = machine.port
    if machine.proxy_jump:
        m["proxy_jump"] = machine.proxy_jump
    if machine.proxy_command:
        m["proxy_command"] = machine.proxy_command
    if machine.password:
        m["password"] = machine.password
    m["daemon_port"] = machine.daemon_port
    if machine.node_path:
        m["node_path"] = machine.node_path
    if machine.default_paths:
        m["default_paths"] = machine.default_paths
    if machine.localhost:
        m["localhost"] = True

    doc["machines"][machine.id] = m

    with open(config_path, "w") as f:
        ryaml.dump(doc, f)

    logger.info(f"Saved machine '{machine.id}' to {config_path}")


def remove_machine_from_config(config: Config, machine_id: str) -> None:
    """
    Remove a machine entry from config.yaml using ruamel.yaml
    to preserve comments and formatting.
    """
    config_path = _get_config_path(config)
    ryaml = YAML()
    ryaml.preserve_quotes = True  # type: ignore[assignment]

    with open(config_path) as f:
        doc = ryaml.load(f)

    if "machines" in doc and doc["machines"] and machine_id in doc["machines"]:
        del doc["machines"][machine_id]
        with open(config_path, "w") as f:
            ryaml.dump(doc, f)
        logger.info(f"Removed machine '{machine_id}' from {config_path}")
    else:
        logger.warning(f"Machine '{machine_id}' not found in {config_path}")


# ─── SSH Config Parser ───


@dataclass
class SSHHostEntry:
    """Parsed entry from ~/.ssh/config."""

    name: str
    hostname: Optional[str] = None
    user: Optional[str] = None
    port: int = 22
    proxy_jump: Optional[str] = None
    proxy_command: Optional[str] = None
    identity_file: Optional[str] = None


def parse_ssh_config(config_path: Optional[str] = None) -> list[SSHHostEntry]:
    """
    Parse ~/.ssh/config (including Include directives) and return
    a list of SSHHostEntry objects.

    Skips wildcard hosts (e.g., Host *) and github.com.
    """
    if config_path is None:
        config_path = str(Path.home() / ".ssh" / "config")

    path = Path(config_path)
    if not path.exists():
        return []

    return _parse_ssh_config_file(path, set())


def _parse_ssh_config_file(path: Path, visited: set[str]) -> list[SSHHostEntry]:
    """Recursively parse an SSH config file, handling Include directives."""
    resolved = path.resolve()
    if str(resolved) in visited:
        return []
    visited.add(str(resolved))

    entries: list[SSHHostEntry] = []
    current: Optional[SSHHostEntry] = None

    try:
        lines = path.read_text().splitlines()
    except (OSError, PermissionError):
        return []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Parse key-value
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue

        key = parts[0].lower()
        value = parts[1].strip().strip('"')

        if key == "include":
            # Resolve include path relative to the SSH config directory
            include_pattern = value
            if include_pattern.startswith("~"):
                include_pattern = str(Path.home()) + include_pattern[1:]
            elif not include_pattern.startswith("/"):
                include_pattern = str(path.parent / include_pattern)

            # Handle glob patterns
            from glob import glob as globfn

            for inc_path in sorted(globfn(include_pattern)):
                entries.extend(_parse_ssh_config_file(Path(inc_path), visited))
            continue

        if key == "host":
            # New host block
            host_name = value
            # Skip wildcard and github entries
            if "*" in host_name or host_name.lower() == "github.com":
                current = None
                continue
            current = SSHHostEntry(name=host_name)
            entries.append(current)
            continue

        if current is None:
            continue

        if key == "hostname":
            current.hostname = value
        elif key == "user":
            current.user = value
        elif key == "port":
            try:
                current.port = int(value)
            except ValueError:
                pass
        elif key == "proxyjump":
            current.proxy_jump = value
        elif key == "proxycommand":
            current.proxy_command = value
        elif key == "identityfile":
            current.identity_file = value

    return entries


def format_ssh_hosts_for_display(entries: list[SSHHostEntry]) -> str:
    """Format SSH host entries for display in chat, with index numbers."""
    if not entries:
        return "No SSH hosts found in `~/.ssh/config`."

    lines = [f"**SSH Hosts** ({len(entries)} found):"]
    lines.append("```")
    for i, e in enumerate(entries, 1):
        host_str = e.hostname or "(no hostname)"
        user_str = f"  user={e.user}" if e.user else ""
        proxy_str = f"  proxy={e.proxy_jump}" if e.proxy_jump else ""
        port_str = f"  port={e.port}" if e.port != 22 else ""
        lines.append(f"{i:3d}. {e.name:<25s} {host_str}{user_str}{proxy_str}{port_str}")
    lines.append("```")
    lines.append("\nReply with the **numbers** of hosts to add (e.g., `1 3 5`).")
    return "\n".join(lines)
