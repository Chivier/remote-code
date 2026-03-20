"""Daemon binary installer — download from GitHub releases or build from source.

The installer tries, in order:
1. Download a pre-built binary matching the current platform from the latest
   GitHub release whose tag matches the running codecast version.
2. If no matching asset exists, build from source (installing Rust if needed).

After installation the binary lives at ~/.codecast/daemon/codecast-daemon
which is one of the paths checked by ``resolve_daemon_binary()``.
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

GITHUB_REPO = "Chivier/codecast"
INSTALL_DIR = Path.home() / ".codecast" / "daemon"
LOCAL_BINARY = INSTALL_DIR / "codecast-daemon"

# Maps (system, machine) → GitHub release asset name.
# Must stay in sync with the CI matrix in .github/workflows/release.yml
PLATFORM_ASSET_MAP: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "codecast-daemon-linux-x64",
    ("linux", "amd64"): "codecast-daemon-linux-x64",
    ("linux", "aarch64"): "codecast-daemon-linux-arm64",
    ("linux", "arm64"): "codecast-daemon-linux-arm64",
    ("darwin", "arm64"): "codecast-daemon-macos-arm64",
    ("darwin", "aarch64"): "codecast-daemon-macos-arm64",
    ("darwin", "x86_64"): "codecast-daemon-macos-x64",
    ("windows", "x86_64"): "codecast-daemon-windows-x64.exe",
    ("windows", "amd64"): "codecast-daemon-windows-x64.exe",
    ("windows", "aarch64"): "codecast-daemon-windows-arm64.exe",
    ("windows", "arm64"): "codecast-daemon-windows-arm64.exe",
}


def get_current_version() -> str:
    """Return the current codecast version string (e.g. '0.2.10')."""
    try:
        from head.__version__ import __version__

        return __version__
    except Exception:
        return ""


def get_expected_asset_name() -> str | None:
    """Return the GitHub release asset name for this platform, or None."""
    system = _platform.system().lower()
    machine = _platform.machine().lower()
    return PLATFORM_ASSET_MAP.get((system, machine))


def _download_with_curl(url: str, dest: Path, on_progress: Callable[[str], None] | None = None) -> bool:
    """Try downloading with curl (handles redirects, proxies, TLS better)."""
    curl = shutil.which("curl")
    if not curl:
        return False
    if on_progress:
        on_progress(f"Downloading with curl...")
    try:
        result = subprocess.run(
            [curl, "-fSL", "--connect-timeout", "15", "--max-time", "120", "-o", str(dest), url],
            capture_output=True,
            text=True,
            timeout=130,
        )
        if result.returncode != 0:
            if on_progress:
                on_progress(f"curl failed: {result.stderr.strip()[:200]}")
            return False
        if on_progress:
            on_progress("Download complete")
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        if on_progress:
            on_progress(f"curl failed: {exc}")
        return False


def _download_with_wget(url: str, dest: Path, on_progress: Callable[[str], None] | None = None) -> bool:
    """Try downloading with wget."""
    wget = shutil.which("wget")
    if not wget:
        return False
    if on_progress:
        on_progress(f"Downloading with wget...")
    try:
        result = subprocess.run(
            [wget, "-q", "--timeout=15", "-O", str(dest), url],
            capture_output=True,
            text=True,
            timeout=130,
        )
        if result.returncode != 0:
            if on_progress:
                on_progress(f"wget failed: {result.stderr.strip()[:200]}")
            return False
        if on_progress:
            on_progress("Download complete")
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        if on_progress:
            on_progress(f"wget failed: {exc}")
        return False


def _download_with_urllib(url: str, dest: Path, on_progress: Callable[[str], None] | None = None) -> bool:
    """Fallback download using Python urllib."""
    if on_progress:
        on_progress("Downloading with urllib...")
    try:
        req = Request(url, headers={"User-Agent": "codecast-installer"})
        with urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            total = resp.headers.get("Content-Length")
            downloaded = 0
            chunk_size = 256 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total:
                    pct = int(downloaded / int(total) * 100)
                    on_progress(f"Downloading... {pct}%")
        if on_progress:
            on_progress("Download complete")
        return True
    except Exception as exc:
        if on_progress:
            on_progress(f"urllib failed: {exc}")
        return False


def _download_url(url: str, dest: Path, on_progress: Callable[[str], None] | None = None) -> None:
    """Download *url* to *dest*, trying curl → wget → urllib."""
    if on_progress:
        on_progress(f"Downloading {url}")
    for method in (_download_with_curl, _download_with_wget, _download_with_urllib):
        dest.unlink(missing_ok=True)
        if method(url, dest, on_progress):
            return
    raise OSError("All download methods failed")


def download_from_release(
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """Download the daemon binary from a matching GitHub release.

    Returns True on success, False if the asset is not available.
    """
    version = get_current_version()
    asset_name = get_expected_asset_name()
    if not asset_name:
        if on_progress:
            on_progress(f"No pre-built binary for this platform ({_platform.system()} {_platform.machine()})")
        return False

    tag = f"v{version}" if version else None
    if not tag:
        if on_progress:
            on_progress("Cannot determine codecast version — skipping download")
        return False

    # Try the matching version tag first, then fall back to latest
    urls_to_try = [
        f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset_name}",
    ]

    for url in urls_to_try:
        try:
            if on_progress:
                on_progress(f"Trying {url}")

            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = INSTALL_DIR / f".{asset_name}.tmp"

            _download_url(url, tmp_path, on_progress)

            # Rename to the canonical local name
            tmp_path.rename(LOCAL_BINARY)
            LOCAL_BINARY.chmod(LOCAL_BINARY.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            # Verify the binary runs
            try:
                result = subprocess.run(
                    [str(LOCAL_BINARY), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if on_progress:
                    on_progress(f"Installed: {result.stdout.strip() or LOCAL_BINARY}")
            except Exception:
                if on_progress:
                    on_progress(f"Installed to {LOCAL_BINARY}")

            return True

        except (URLError, OSError) as exc:
            logger.debug("Download failed for %s: %s", url, exc)
            tmp_path = INSTALL_DIR / f".{asset_name}.tmp"
            tmp_path.unlink(missing_ok=True)
            if on_progress:
                on_progress(f"Download failed: {exc}")
            continue

    return False


def _has_rust() -> bool:
    """Return True if cargo is available."""
    return shutil.which("cargo") is not None


def _install_rust(on_progress: Callable[[str], None] | None = None) -> bool:
    """Install Rust via rustup. Returns True on success."""
    if on_progress:
        on_progress("Installing Rust via rustup...")

    try:
        result = subprocess.run(
            ["sh", "-c", "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            if on_progress:
                on_progress(f"Rust install failed: {result.stderr[:200]}")
            return False

        # Source cargo env so it's on PATH for this process
        cargo_bin = Path.home() / ".cargo" / "bin"
        if cargo_bin.exists():
            os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"

        if on_progress:
            on_progress("Rust installed successfully")
        return True

    except Exception as exc:
        if on_progress:
            on_progress(f"Rust install failed: {exc}")
        return False


def build_from_source(
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """Clone the repo and build the daemon from source.

    Installs Rust first if not available. Returns True on success.
    """
    if not _has_rust():
        if not _install_rust(on_progress):
            return False

    if on_progress:
        on_progress("Cloning codecast repository...")

    with tempfile.TemporaryDirectory(prefix="codecast-build-") as tmpdir:
        try:
            # Clone
            result = subprocess.run(
                ["git", "clone", "--depth", "1", f"https://github.com/{GITHUB_REPO}.git", tmpdir],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                if on_progress:
                    on_progress(f"Git clone failed: {result.stderr[:200]}")
                return False

            if on_progress:
                on_progress("Building daemon (cargo build --release)... this may take a few minutes")

            # Build
            result = subprocess.run(
                ["cargo", "build", "--release"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                if on_progress:
                    on_progress(f"Build failed: {result.stderr[:300]}")
                return False

            # Copy binary to install dir
            built = Path(tmpdir) / "target" / "release" / "codecast-daemon"
            if not built.exists():
                if on_progress:
                    on_progress("Build succeeded but binary not found")
                return False

            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(built), str(LOCAL_BINARY))
            LOCAL_BINARY.chmod(LOCAL_BINARY.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            if on_progress:
                on_progress(f"Daemon built and installed to {LOCAL_BINARY}")
            return True

        except subprocess.TimeoutExpired:
            if on_progress:
                on_progress("Build timed out (10 min limit)")
            return False
        except Exception as exc:
            if on_progress:
                on_progress(f"Build failed: {exc}")
            return False


def install_daemon(
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """Install the daemon binary: try download first, fall back to build.

    Returns True if the daemon was installed successfully.
    """
    asset = get_expected_asset_name()
    if on_progress:
        system = _platform.system()
        machine = _platform.machine()
        on_progress(f"Platform: {system} {machine}")
        if asset:
            on_progress(f"Looking for release asset: {asset}")
        else:
            on_progress(f"No pre-built binary for {system} {machine}, will build from source")

    # Step 1: Try downloading pre-built binary
    if asset and download_from_release(on_progress):
        return True

    if on_progress:
        on_progress("Pre-built binary not available, building from source...")

    # Step 2: Build from source
    return build_from_source(on_progress)
