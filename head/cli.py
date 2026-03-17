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
    codecast bot start    -> start v1 bot (Discord/Telegram)
    codecast webui        -> start web UI
"""

from __future__ import annotations

import argparse
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
        "--config", "-c",
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

    # bot -------------------------------------------------------------
    sub_bot = subparsers.add_parser("bot", help="Run the v1 chat bot")
    sub_bot.add_argument("bot_action", nargs="?", default="start", help="Bot action (default: start)")

    # webui -----------------------------------------------------------
    subparsers.add_parser("webui", help="Start the web UI")

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Port file helpers
# ---------------------------------------------------------------------------

_PORT_FILE = Path.home() / ".codecast" / "daemon.port"


def _read_port_file() -> int | None:
    """Return the daemon port from the port file, or None."""
    try:
        return int(_PORT_FILE.read_text().strip())
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
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"Daemon started (pid={proc.pid})")


def _cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running daemon."""
    port = _read_port_file()
    if port is not None and _daemon_healthy(port):
        # Try graceful shutdown via signal
        try:
            subprocess.run(["pkill", "-f", "codecast-daemon"], check=False)
        except FileNotFoundError:
            pass
    else:
        # Attempt pkill anyway
        try:
            subprocess.run(["pkill", "-f", "codecast-daemon"], check=False)
        except FileNotFoundError:
            pass

    # Remove port file
    try:
        _PORT_FILE.unlink(missing_ok=True)
    except Exception:
        pass
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
    """Show daemon status, Claude availability, and peer count."""
    port = _read_port_file()
    if port is None:
        print("Daemon: not running (no port file)")
    elif _daemon_healthy(port):
        print(f"Daemon: running on port {port}")
    else:
        print(f"Daemon: port file exists (port {port}) but not responding")

    # Claude CLI availability
    claude_available = subprocess.run(
        ["which", "claude"], capture_output=True
    ).returncode == 0
    print(f"Claude CLI: {'available' if claude_available else 'not found'}")

    # Peer count
    try:
        from head.config_v2 import load_config_v2
        cfg_path = args.config or str(Path.home() / ".codecast" / "config.yaml")
        cfg = load_config_v2(cfg_path)
        peers = getattr(cfg, "peers", []) or []
        print(f"Peers: {len(peers)}")
    except Exception:
        print("Peers: unable to load config")


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
    """List active sessions (placeholder)."""
    print("Sessions listing is not yet implemented.")


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


def _cmd_bot(args: argparse.Namespace) -> None:
    """Delegate to the v1 bot entry point."""
    from head.main import cli_main
    cli_main()


def _cmd_webui(args: argparse.Namespace) -> None:
    """Start the web UI (placeholder)."""
    print("Web UI is not yet implemented.")


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
    "bot": _cmd_bot,
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
