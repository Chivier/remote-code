"""CLI entry point for the ``codecast`` command.

Provides subcommand dispatch via argparse:
    codecast              -> TUI (default, no subcommand)
    codecast start        -> start daemon
    codecast stop         -> stop daemon
    codecast restart      -> restart daemon
    codecast update       -> git pull + restart
    codecast status       -> show daemon/claude status
    codecast peers        -> list peers
    codecast sessions     -> list sessions
    codecast token ...    -> generate/list/revoke tokens
    codecast head start   -> start head node (Discord/Telegram/Lark)
    codecast webui        -> start web UI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments and return the resulting namespace."""
    from head.__version__ import __version__

    parser = argparse.ArgumentParser(
        prog="codecast",
        description="Codecast - interact with Claude CLI on remote machines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "daemon commands:\n"
            "  start       Start the daemon\n"
            "  stop        Stop the daemon\n"
            "  restart     Restart the daemon\n"
            "\n"
            "service commands:\n"
            "  head        Start the head node (Discord/Telegram/Lark)\n"
            "  webui       Start/stop the web UI\n"
            "\n"
            "info commands:\n"
            "  status      Show component status\n"
            "  peers       List configured peers\n"
            "  sessions    List active sessions\n"
            "\n"
            "management:\n"
            "  token       Manage auth tokens\n"
            "  update      Git pull and restart\n"
            "  uninstall   Remove codecast data and daemon binary\n"
            "  completion  Generate shell completion script\n"
            "\n"
            "Run 'codecast <command> --help' for details."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"codecast {__version__}",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        help="Path to config YAML file",
    )

    subparsers = parser.add_subparsers(dest="command")

    # start -----------------------------------------------------------
    sub_start = subparsers.add_parser("start", help="Start the daemon")
    sub_start.add_argument("--config", "-c", dest="config", default=None, help="Path to config YAML file")

    # stop ------------------------------------------------------------
    subparsers.add_parser("stop", help="Stop the daemon")

    # restart ---------------------------------------------------------
    subparsers.add_parser("restart", help="Restart the daemon")

    # update ----------------------------------------------------------
    subparsers.add_parser("update", help="Git pull and restart")

    # status ----------------------------------------------------------
    subparsers.add_parser("status", help="Show component status")

    # peers -----------------------------------------------------------
    subparsers.add_parser("peers", help="List configured peers")

    # sessions --------------------------------------------------------
    subparsers.add_parser("sessions", help="List active sessions")

    # token -----------------------------------------------------------
    sub_token = subparsers.add_parser("token", help="Manage auth tokens")
    token_sub = sub_token.add_subparsers(dest="token_action")
    token_sub.add_parser("generate", help="Generate a new token")
    token_sub.add_parser("list", help="List all tokens")
    tok_revoke = token_sub.add_parser("revoke", help="Revoke a token")
    tok_revoke.add_argument("token_value", help="Token string to revoke")

    # head (formerly "bot") --------------------------------------------
    sub_head = subparsers.add_parser("head", help="Start the head node (Discord/Telegram/Lark)")
    sub_head.add_argument("head_action", nargs="?", default="start", help="Action (default: start)")
    sub_head.add_argument("--config", "-c", dest="config", default=None, help="Path to config YAML file")
    sub_head.add_argument("--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt")

    # Keep "bot" as a hidden alias for backwards compatibility
    sub_bot = subparsers.add_parser("bot")
    sub_bot.add_argument("head_action", nargs="?", default="start")
    sub_bot.add_argument("--config", "-c", dest="config", default=None)
    sub_bot.add_argument("--yes", "-y", action="store_true", default=False)

    # webui -----------------------------------------------------------
    sub_webui = subparsers.add_parser("webui", help="Start/stop the web UI")
    sub_webui.add_argument(
        "webui_action",
        nargs="?",
        default="start",
        choices=["start", "stop", "status"],
        help="Action to perform (default: start)",
    )
    sub_webui.add_argument("--port", "-p", type=int, default=None, help="WebUI port")
    sub_webui.add_argument("--bind", "-b", default=None, help="Bind address")

    # uninstall -------------------------------------------------------
    sub_uninstall = subparsers.add_parser("uninstall", help="Remove codecast data and daemon binary")
    sub_uninstall.add_argument(
        "--keep-config",
        action="store_true",
        default=False,
        help="Keep config.yaml, daemon.yaml, and tokens.yaml",
    )
    sub_uninstall.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt",
    )

    # completion ------------------------------------------------------
    sub_completion = subparsers.add_parser("completion", help="Generate shell completion script")
    sub_completion.add_argument(
        "shell",
        choices=["bash", "zsh", "fish"],
        help="Shell to generate completion for",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Port file helpers  (implementations live in process_monitor; re-exported
# here with underscore aliases for backward compatibility)
# ---------------------------------------------------------------------------

from head.process_monitor import (  # noqa: E402
    CODECAST_DIR as _CODECAST_DIR,
    DAEMON_PID_FILE as _DAEMON_PID_FILE,
    HEAD_PID_FILE as _HEAD_PID_FILE,
    PORT_FILE as _PORT_FILE,
    WEBUI_PID_FILE as _WEBUI_PID_FILE,
    WEBUI_PORT_FILE as _WEBUI_PORT_FILE,
    daemon_healthy as _daemon_healthy,
    find_process as _find_process,
    pid_alive as _pid_alive,
    read_pid_file as _read_pid_file,
    read_port_file as _read_port_file,
)


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if *port* on *host* is available for binding."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _run_tui(args: argparse.Namespace) -> None:
    """Launch the interactive TUI."""
    from head.tui.app import CodecastApp

    app = CodecastApp(config_path=args.config)
    app.run()


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the daemon as a background subprocess."""
    from head.daemon_installer import get_current_version, get_daemon_version

    # Check if already running
    port = _read_port_file()
    if port is not None and _daemon_healthy(port):
        # Check for version mismatch — auto-restart if binary was updated
        pkg_version = get_current_version()
        daemon_version = get_daemon_version()
        if pkg_version and daemon_version and pkg_version != daemon_version:
            print(f"Daemon version mismatch: running {daemon_version}, installed {pkg_version}. Restarting...")
            _cmd_stop(args)
        else:
            print(f"Daemon already running on port {port}")
            return

    # Resolve daemon binary
    daemon_bin = None
    try:
        from head.peer_manager import resolve_daemon_binary

        daemon_bin = resolve_daemon_binary()
    except (ImportError, Exception):
        pass

    if daemon_bin is None:
        print("Error: could not find codecast-daemon binary.", file=sys.stderr)
        print("Build it with: cargo build --release", file=sys.stderr)
        print("Then copy to: ~/.codecast/daemon/codecast-daemon", file=sys.stderr)
        sys.exit(1)

    config_path = args.config
    cmd = [daemon_bin]
    if config_path:
        cmd.extend(["--config", config_path])

    print(f"Starting daemon: {daemon_bin}")
    _CODECAST_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _DAEMON_PID_FILE.write_text(str(proc.pid))
    print(f"Daemon started (pid={proc.pid})")


def _cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running daemon and wait for it to exit."""
    import time

    stopped = False
    daemon_pid = _read_pid_file(_DAEMON_PID_FILE)

    # Try PID file first (most reliable)
    if daemon_pid is not None and _pid_alive(daemon_pid):
        try:
            os.kill(daemon_pid, signal.SIGTERM)
            stopped = True
        except ProcessLookupError:
            pass

    # Fallback to pkill if PID file didn't work
    if not stopped:
        try:
            subprocess.run(["pkill", "-f", "codecast-daemon"], check=False)
        except FileNotFoundError:
            pass

    # Wait for the process to actually die so the port is freed
    if daemon_pid is not None and _pid_alive(daemon_pid):
        for _ in range(50):  # up to 5 seconds
            if not _pid_alive(daemon_pid):
                break
            time.sleep(0.1)
        else:
            # Still alive after timeout — force kill
            try:
                os.kill(daemon_pid, signal.SIGKILL)
                time.sleep(0.1)
            except ProcessLookupError:
                pass

    # Remove PID and port files
    _DAEMON_PID_FILE.unlink(missing_ok=True)
    _PORT_FILE.unlink(missing_ok=True)
    print("Daemon stopped.")


def _cmd_restart(args: argparse.Namespace) -> None:
    """Restart the daemon by re-executing the current process."""
    _cmd_stop(args)
    os.execv(sys.executable, [sys.executable, "-m", "head.cli", "start"])


def _cmd_update(args: argparse.Namespace) -> None:
    """Git pull and restart."""
    # Stop the running daemon first so the new binary is used after restart
    _cmd_stop(args)
    print("Pulling latest code...")
    result = subprocess.run(["git", "pull", "--ff-only"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"git pull failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("Restarting...")
    os.execv(sys.executable, [sys.executable, "-m", "head.cli", "start"])


def _cmd_status(args: argparse.Namespace) -> None:
    """Show status of all codecast components."""
    # ── Head Node ──
    head_pid = _read_pid_file(_HEAD_PID_FILE)
    if head_pid is not None and _pid_alive(head_pid):
        print(f"Head Node:  running (pid={head_pid})")
    elif head_pid is not None:
        print("Head Node:  stale PID file (not running)")
    else:
        print("Head Node:  not running")

    # ── Daemon ──
    port = _read_port_file()
    daemon_pid = _read_pid_file(_DAEMON_PID_FILE) or _find_process("codecast-daemon")
    if port is not None and _daemon_healthy(port):
        pid_part = f" (pid={daemon_pid})" if daemon_pid else ""
        print(f"Daemon:     running on port {port}{pid_part}")
    elif port is not None:
        print(f"Daemon:     port file exists (port {port}) but not responding")
    else:
        print("Daemon:     not running (no port file)")

    # ── WebUI ──
    webui_pid = _read_pid_file(_WEBUI_PID_FILE)
    webui_port = _read_pid_file(_WEBUI_PORT_FILE)
    if webui_pid is not None and _pid_alive(webui_pid):
        print(f"WebUI:      running on http://127.0.0.1:{webui_port} (pid={webui_pid})")
    else:
        print("WebUI:      not running")

    # ── Claude CLI ──
    claude_result = subprocess.run(
        ["which", "claude"],
        capture_output=True,
        text=True,
    )
    if claude_result.returncode == 0:
        claude_path = claude_result.stdout.strip()
        print(f"Claude CLI: available ({claude_path})")
    else:
        print("Claude CLI: not found")

    # ── Peers ──
    try:
        from head.config import load_config

        cfg_path = args.config
        if not cfg_path:
            # Try ~/.codecast/config.yaml first, then ./config.yaml
            for candidate in [
                str(Path.home() / ".codecast" / "config.yaml"),
                "config.yaml",
            ]:
                if Path(candidate).exists():
                    cfg_path = candidate
                    break
        if cfg_path:
            cfg = load_config(cfg_path)
            peers = getattr(cfg, "peers", {}) or {}
            print(f"Peers:      {len(peers)} machines configured")
        else:
            print("Peers:      no config file found")
    except Exception as exc:
        print(f"Peers:      unable to load config ({exc})")


def _cmd_peers(args: argparse.Namespace) -> None:
    """List configured peers."""
    try:
        from head.config import load_config

        cfg_path = args.config or str(Path.home() / ".codecast" / "config.yaml")
        cfg = load_config(cfg_path)
        peers = cfg.peers
        if not peers:
            print("No peers configured.")
            return
        for peer_id, peer_cfg in peers.items():
            host = getattr(peer_cfg, "ssh_host", None) or peer_cfg.transport
            print(f"  {peer_id} ({host})")
    except FileNotFoundError:
        print("Config file not found. Use --config to specify path.")
    except Exception as exc:
        print(f"Error loading peers: {exc}")


def _cmd_sessions(args: argparse.Namespace) -> None:
    """List sessions from the SessionRouter database."""
    from head.session_router import SessionRouter

    # Try common DB locations
    candidates = [
        Path.home() / ".codecast" / "sessions.db",
        Path(__file__).parent / "sessions.db",
    ]
    db_path = None
    for candidate in candidates:
        if candidate.exists():
            db_path = candidate
            break

    if db_path is None:
        print("No sessions database found.")
        return

    router = SessionRouter(str(db_path))
    sessions = router.list_sessions()
    if not sessions:
        print("No sessions.")
        return

    # Print table header
    print(f"{'Name':<20} {'Machine':<15} {'Path':<30} {'Mode':<6} {'Status':<10}")
    print("-" * 85)
    for s in sessions:
        name = s.name or s.daemon_session_id[:8]
        path = s.path if len(s.path) <= 30 else "..." + s.path[-27:]
        print(f"{name:<20} {s.machine_id:<15} {path:<30} {s.mode:<6} {s.status:<10}")


def _cmd_token(args: argparse.Namespace) -> None:
    """Manage auth tokens: generate / list / revoke."""
    from head.token_manager import TokenManager

    token_file = Path.home() / ".codecast" / "tokens.yaml"
    mgr = TokenManager(str(token_file))

    action = getattr(args, "token_action", None)
    if action == "generate":
        entry = mgr.generate()
        print(f"Generated token: {entry['token']}")
    elif action == "list":
        tokens = mgr.list()
        if not tokens:
            print("No tokens.")
            return
        for t in tokens:
            print(f"  {t['token']}  (label={t.get('label', '-')}, created={t.get('created', '?')})")
    elif action == "revoke":
        token_val = getattr(args, "token_value", None)
        if not token_val:
            print("Error: token value required", file=sys.stderr)
            sys.exit(1)
        if mgr.revoke(token_val):
            print(f"Revoked: {token_val}")
        else:
            print(f"Token not found: {token_val}")
    else:
        print("Usage: codecast token {generate|list|revoke}")


def _cmd_head(args: argparse.Namespace) -> None:
    """Start the head node with config discovery and confirmation."""
    # Resolve config path: --config flag > ~/.codecast/config.yaml > ./config.yaml
    cfg_path = getattr(args, "config", None)
    if not cfg_path:
        for candidate in [
            str(Path.home() / ".codecast" / "config.yaml"),
            "config.yaml",
        ]:
            if Path(candidate).exists():
                cfg_path = candidate
                break

    if not cfg_path or not Path(cfg_path).exists():
        print("No config file found.", file=sys.stderr)
        print("Run 'codecast' TUI to set up, or specify --config.", file=sys.stderr)
        sys.exit(1)

    # Load config and show summary
    try:
        from head.config import load_config

        cfg = load_config(cfg_path)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        sys.exit(1)

    bots_configured = []
    if cfg.bot:
        if cfg.bot.discord and getattr(cfg.bot.discord, "token", None):
            bots_configured.append("Discord")
        if cfg.bot.telegram and getattr(cfg.bot.telegram, "token", None):
            bots_configured.append("Telegram")
        if getattr(cfg.bot, "lark", None) and getattr(cfg.bot.lark, "app_id", None):
            bots_configured.append("Lark")

    if not bots_configured:
        print("No bot tokens configured.", file=sys.stderr)
        print("Run 'codecast' TUI to set up Discord/Telegram/Lark tokens.", file=sys.stderr)
        sys.exit(1)

    peers = getattr(cfg, "peers", {}) or {}
    print(f"Config:  {cfg_path}")
    print(f"Bots:    {', '.join(bots_configured)}")
    print(f"Peers:   {len(peers)} configured")

    # Prompt for confirmation unless --yes
    skip_confirm = getattr(args, "yes", False)
    if not skip_confirm:
        try:
            answer = input("Start head with this config? [Y/n] ").strip().lower()
            if answer and answer not in ("y", "yes"):
                print("Aborted.")
                return
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

    from head.main import cli_main

    cli_main(cfg_path)


def _cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove codecast data directory and daemon binary."""
    codecast_dir = Path.home() / ".codecast"
    keep_config = getattr(args, "keep_config", False)
    skip_confirm = getattr(args, "yes", False)

    if not codecast_dir.exists():
        print("Nothing to remove — ~/.codecast/ does not exist.")
        return

    # Build list of paths to remove
    always_remove = [
        "daemon/",
        "daemon.log",
        "file-pool/",
        "downloads/",
        "sessions.db",
        "skills/",
    ]
    # PID and port files (glob)
    for p in codecast_dir.glob("*.pid"):
        always_remove.append(p.name)
    for p in codecast_dir.glob("*.port"):
        always_remove.append(p.name)

    config_files = ["config.yaml", "daemon.yaml", "tokens.yaml"]

    to_remove = list(always_remove)
    if not keep_config:
        to_remove.extend(config_files)

    # Filter to things that actually exist
    existing = []
    for name in to_remove:
        path = codecast_dir / name
        if path.exists():
            existing.append(path)

    if not existing:
        if keep_config:
            print("Nothing to remove (only config files remain, preserved by --keep-config).")
        else:
            print("Nothing to remove — ~/.codecast/ is empty.")
        return

    # Show what will be removed
    print("The following will be removed:")
    for p in existing:
        suffix = "/" if p.is_dir() else ""
        print(f"  ~/.codecast/{p.name}{suffix}")
    if keep_config:
        print(f"\nKept (--keep-config): {', '.join(config_files)}")

    if not skip_confirm:
        try:
            answer = input("\nProceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

    # Stop running processes first
    print("Stopping running processes...")
    _stop_all_processes()

    # Remove files
    import shutil

    removed = []
    for p in existing:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(p.name)
        except OSError as exc:
            print(f"  Warning: could not remove {p}: {exc}")

    # Remove the directory itself if empty
    try:
        if codecast_dir.exists() and not any(codecast_dir.iterdir()):
            codecast_dir.rmdir()
            print(f"\nRemoved ~/.codecast/ ({len(removed)} items)")
        else:
            remaining = list(codecast_dir.iterdir())
            print(f"\nRemoved {len(removed)} items from ~/.codecast/")
            if remaining:
                print(f"Remaining: {', '.join(p.name for p in remaining)}")
    except OSError:
        print(f"\nRemoved {len(removed)} items from ~/.codecast/")

    print("\nTo also uninstall the Python package:")
    print("  pip uninstall codecast")


def _stop_all_processes() -> None:
    """Stop daemon, head, and webui processes."""
    for pid_file, label in [
        (_DAEMON_PID_FILE, "daemon"),
        (_HEAD_PID_FILE, "head"),
        (_WEBUI_PID_FILE, "webui"),
    ]:
        pid = _read_pid_file(pid_file)
        if pid is not None and _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"  Stopped {label} (pid={pid})")
            except ProcessLookupError:
                pass


def _cmd_completion(args: argparse.Namespace) -> None:
    """Generate shell completion script for bash, zsh, or fish."""
    shell = args.shell
    if shell == "bash":
        print(_completion_bash())
    elif shell == "zsh":
        print(_completion_zsh())
    elif shell == "fish":
        print(_completion_fish())


def _completion_bash() -> str:
    return """\
_codecast() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    commands="start stop restart status peers sessions token head webui update uninstall completion"

    case "$prev" in
        codecast)
            COMPREPLY=( $(compgen -W "$commands --version --help --config" -- "$cur") )
            return 0
            ;;
        token)
            COMPREPLY=( $(compgen -W "generate list revoke" -- "$cur") )
            return 0
            ;;
        webui)
            COMPREPLY=( $(compgen -W "start stop status" -- "$cur") )
            return 0
            ;;
        head|bot)
            COMPREPLY=( $(compgen -W "start stop --config --yes" -- "$cur") )
            return 0
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "$cur") )
            return 0
            ;;
        uninstall)
            COMPREPLY=( $(compgen -W "--keep-config --yes" -- "$cur") )
            return 0
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "--version --help --config" -- "$cur") )
    fi
}

complete -F _codecast codecast"""


def _completion_zsh() -> str:
    return '''\
#compdef codecast

_codecast() {
    local -a commands
    commands=(
        'start:Start the daemon'
        'stop:Stop the daemon'
        'restart:Restart the daemon'
        'status:Show component status'
        'peers:List configured peers'
        'sessions:List active sessions'
        'token:Manage auth tokens'
        'head:Start the head node'
        'webui:Start/stop the web UI'
        'update:Git pull and restart'
        'uninstall:Remove codecast data and daemon binary'
        'completion:Generate shell completion script'
    )

    _arguments -C \\
        '--version[Show version]' \\
        '--help[Show help]' \\
        '(-c --config)'{-c,--config}'[Config file]:file:_files' \\
        '1:command:->command' \\
        '*::arg:->args'

    case "$state" in
        command)
            _describe -t commands 'codecast command' commands
            ;;
        args)
            case "${words[1]}" in
                token)
                    _values 'action' generate list revoke
                    ;;
                webui)
                    _values 'action' start stop status
                    ;;
                head)
                    _values 'action' start stop
                    ;;
                completion)
                    _values 'shell' bash zsh fish
                    ;;
                uninstall)
                    _arguments '--keep-config[Keep config files]' '--yes[Skip confirmation]'
                    ;;
            esac
            ;;
    esac
}

_codecast "$@"'''


def _completion_fish() -> str:
    return '''\
# codecast completions for fish

set -l commands start stop restart status peers sessions token head webui update uninstall completion

complete -c codecast -f
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -l version -d "Show version"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -l help -d "Show help"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -s c -l config -r -d "Config file"

complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a start -d "Start the daemon"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a stop -d "Stop the daemon"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a restart -d "Restart the daemon"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a status -d "Show component status"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a peers -d "List configured peers"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a sessions -d "List active sessions"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a token -d "Manage auth tokens"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a head -d "Start the head node"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a webui -d "Start/stop the web UI"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a update -d "Git pull and restart"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a uninstall -d "Remove codecast data"
complete -c codecast -n "not __fish_seen_subcommand_from $commands" -a completion -d "Generate completion script"

complete -c codecast -n "__fish_seen_subcommand_from token" -a "generate list revoke"
complete -c codecast -n "__fish_seen_subcommand_from webui" -a "start stop status"
complete -c codecast -n "__fish_seen_subcommand_from head" -a "start stop"
complete -c codecast -n "__fish_seen_subcommand_from completion" -a "bash zsh fish"
complete -c codecast -n "__fish_seen_subcommand_from uninstall" -l keep-config -d "Keep config files"
complete -c codecast -n "__fish_seen_subcommand_from uninstall" -s y -l yes -d "Skip confirmation"'''


def _cmd_webui(args: argparse.Namespace) -> None:
    """Dispatch webui start / stop / status."""
    action = getattr(args, "webui_action", "start")
    if action == "stop":
        _webui_stop()
    elif action == "status":
        _webui_status()
    else:
        _webui_start(args)


def _webui_start(args: argparse.Namespace) -> None:
    """Start the web UI (foreground or background)."""
    # Load config (optional -- webui works without it)
    config = None
    try:
        from head.config import load_config

        cfg_path = args.config or str(Path.home() / ".codecast" / "config.yaml")
        config = load_config(cfg_path)
    except Exception:
        pass

    # Determine bind/port from args -> config -> defaults
    port = getattr(args, "port", None) or 31949
    bind = getattr(args, "bind", None) or "127.0.0.1"
    if config and config.bot and config.bot.webui:
        if getattr(args, "port", None) is None:
            port = config.bot.webui.port or port
        if getattr(args, "bind", None) is None:
            bind = config.bot.webui.host or bind

    # Check if already running
    existing_pid = _read_pid_file(_WEBUI_PID_FILE)
    if existing_pid is not None and _pid_alive(existing_pid):
        existing_port = _read_pid_file(_WEBUI_PORT_FILE)
        print(f"WebUI already running (pid={existing_pid}, port={existing_port})")
        return

    # Check port availability
    if not _port_available(port, bind):
        print(f"Error: port {port} on {bind} is already in use.", file=sys.stderr)
        sys.exit(1)

    # Write PID and port files
    _CODECAST_DIR.mkdir(parents=True, exist_ok=True)
    _WEBUI_PID_FILE.write_text(str(os.getpid()))
    _WEBUI_PORT_FILE.write_text(str(port))

    print(f"Starting WebUI on http://{bind}:{port}")
    try:
        asyncio.run(_start_webui(config, bind, port))
    finally:
        _WEBUI_PID_FILE.unlink(missing_ok=True)
        _WEBUI_PORT_FILE.unlink(missing_ok=True)


def _webui_stop() -> None:
    """Stop a running WebUI process."""
    pid = _read_pid_file(_WEBUI_PID_FILE)
    if pid is None:
        print("WebUI is not running (no PID file).")
        return
    if not _pid_alive(pid):
        print(f"WebUI PID {pid} is not running; cleaning up stale files.")
        _WEBUI_PID_FILE.unlink(missing_ok=True)
        _WEBUI_PORT_FILE.unlink(missing_ok=True)
        return

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"WebUI stopped (pid={pid}).")
    except ProcessLookupError:
        print(f"WebUI process {pid} already gone.")
    _WEBUI_PID_FILE.unlink(missing_ok=True)
    _WEBUI_PORT_FILE.unlink(missing_ok=True)


def _webui_status() -> None:
    """Show WebUI status."""
    pid = _read_pid_file(_WEBUI_PID_FILE)
    port = _read_pid_file(_WEBUI_PORT_FILE)
    if pid is not None and _pid_alive(pid):
        bind = "127.0.0.1"
        print(f"WebUI: running on http://{bind}:{port} (pid={pid})")
    elif pid is not None:
        print(f"WebUI: stale PID file (pid={pid}, not running)")
    else:
        print("WebUI: not running")


async def _start_webui(config, bind: str, port: int) -> None:
    """Run the WebUI server until interrupted."""
    from head.webui.server import create_app
    from aiohttp import web

    app = await create_app(config, bind=bind)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, bind, port)
    await site.start()
    print(f"WebUI running at http://{bind}:{port}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, callable] = {
    "start": _cmd_start,
    "stop": _cmd_stop,
    "restart": _cmd_restart,
    "update": _cmd_update,
    "status": _cmd_status,
    "peers": _cmd_peers,
    "sessions": _cmd_sessions,
    "token": _cmd_token,
    "head": _cmd_head,
    "bot": _cmd_head,
    "webui": _cmd_webui,
    "uninstall": _cmd_uninstall,
    "completion": _cmd_completion,
}


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the ``codecast`` command."""
    args = parse_args(argv)
    command = args.command

    if command is None:
        _run_tui(args)
        return

    handler = _COMMANDS.get(command)
    if handler is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
