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
    parser = argparse.ArgumentParser(
        prog="codecast",
        description="Codecast - interact with Claude CLI on remote machines",
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
    subparsers.add_parser("status", help="Show daemon status")

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

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------

_CODECAST_DIR = Path.home() / ".codecast"
_PORT_FILE = _CODECAST_DIR / "daemon.port"
_DAEMON_PID_FILE = _CODECAST_DIR / "daemon.pid"
_HEAD_PID_FILE = _CODECAST_DIR / "head.pid"
_WEBUI_PID_FILE = _CODECAST_DIR / "webui.pid"
_WEBUI_PORT_FILE = _CODECAST_DIR / "webui.port"


def _read_port_file() -> int | None:
    """Return the daemon port from the port file, or None."""
    try:
        return int(_PORT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if *port* on *host* is available for binding."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pid_alive(pid: int) -> bool:
    """Return True if a process with *pid* exists."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_pid_file(path: Path) -> int | None:
    """Read a PID from a file, returning None if missing or invalid."""
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _daemon_healthy(port: int) -> bool:
    """Quick health check against localhost:<port>."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
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
    # Check if already running
    port = _read_port_file()
    if port is not None and _daemon_healthy(port):
        print(f"Daemon already running on port {port}")
        return

    # Resolve daemon binary
    daemon_bin: str | None = None
    try:
        from head.peer_manager import resolve_daemon_binary

        daemon_bin = resolve_daemon_binary()
    except (ImportError, Exception):
        # Fallback: look for bundled binary
        candidates = [
            Path(__file__).parent / "bin" / "codecast-daemon",
            Path.home() / ".codecast" / "daemon" / "codecast-daemon",
        ]
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                daemon_bin = str(c)
                break

    if daemon_bin is None:
        print("Error: could not find codecast-daemon binary.", file=sys.stderr)
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
    """Stop the running daemon."""
    stopped = False

    # Try PID file first (most reliable)
    daemon_pid = _read_pid_file(_DAEMON_PID_FILE)
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
        from head.config_v2 import load_config_v2

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
            cfg = load_config_v2(cfg_path)
            peers = getattr(cfg, "peers", {}) or {}
            print(f"Peers:      {len(peers)} machines configured")
        else:
            print("Peers:      no config file found")
    except Exception as exc:
        print(f"Peers:      unable to load config ({exc})")


def _find_process(name: str) -> int | None:
    """Find a process by name using pgrep, returning its PID or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Return first PID (skip our own)
            for line in result.stdout.strip().splitlines():
                pid = int(line.strip())
                if pid != os.getpid():
                    return pid
    except (FileNotFoundError, ValueError):
        pass
    return None


def _cmd_peers(args: argparse.Namespace) -> None:
    """List configured peers."""
    try:
        from head.config_v2 import load_config_v2

        cfg_path = args.config or str(Path.home() / ".codecast" / "config.yaml")
        cfg = load_config_v2(cfg_path)
        peers = getattr(cfg, "peers", []) or []
        if not peers:
            print("No peers configured.")
            return
        for p in peers:
            name = getattr(p, "name", None) or getattr(p, "host", "unknown")
            host = getattr(p, "host", "?")
            print(f"  {name} ({host})")
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
        from head.config_v2 import load_config_v2

        cfg = load_config_v2(cfg_path)
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
        from head.config_v2 import load_config_v2

        cfg_path = args.config or str(Path.home() / ".codecast" / "config.yaml")
        config = load_config_v2(cfg_path)
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
