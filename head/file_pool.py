"""
File Pool - manages local cache of uploaded files.

Stores files from any platform adapter to a local directory, generates unique
file IDs, and handles LRU eviction when the pool exceeds its configured max size.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Extension to MIME type mapping (fallback when Discord doesn't provide content_type)
_EXT_TO_MIME: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}


@dataclass
class FileEntry:
    """A file cached in the local pool."""

    file_id: str  # Unique ID: {session_prefix}_{uuid8}
    original_name: str  # Original filename from Discord
    local_path: Path  # Full path in the pool directory
    size: int  # File size in bytes
    mime_type: str  # Content type (from Discord or guessed from extension)
    created_at: float  # time.time() when downloaded


def _sanitize_filename(name: str) -> str:
    """
    Sanitize a filename for safe filesystem use.

    Strips path separators, null bytes, leading dots, and shell metacharacters.
    Replaces spaces with hyphens. Limits to 200 characters (preserving extension).
    """
    # Remove path separators and null bytes
    name = name.replace("/", "").replace("\\", "").replace("\0", "")
    # Remove leading dots (hidden files)
    name = name.lstrip(".")
    # Remove shell metacharacters
    name = re.sub(r'[;&|$`(){}\[\]!#~]', "", name)
    # Replace spaces with hyphens
    name = name.replace(" ", "-")
    # Remove consecutive hyphens
    name = re.sub(r"-+", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")

    if not name:
        return "unnamed"

    # Limit length while preserving extension
    if len(name) > 200:
        stem = Path(name).stem
        suffix = Path(name).suffix
        max_stem = 200 - len(suffix)
        if max_stem > 0:
            name = stem[:max_stem] + suffix
        else:
            name = name[:200]

    return name


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from file extension."""
    ext = Path(filename).suffix.lower()
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


class FilePool:
    """
    Local file cache for uploaded files from any platform.

    Manages a directory of downloaded files with LRU eviction
    when total size exceeds the configured maximum.
    """

    def __init__(
        self,
        max_size: int = 1073741824,
        pool_dir: Optional[Path] = None,
        allowed_types: Optional[list[str]] = None,
    ):
        """
        Args:
            max_size: Maximum total bytes for the pool (default 1GB).
            pool_dir: Local directory for cached files.
            allowed_types: List of MIME type patterns (supports wildcards like "image/*").
        """
        self.max_size = max_size
        self.pool_dir = pool_dir or Path.home() / ".remote-claude" / "file-pool"
        self.allowed_types = allowed_types or [
            "text/plain",
            "text/markdown",
            "application/pdf",
            "image/*",
            "video/*",
            "audio/*",
        ]
        self._entries: dict[str, FileEntry] = {}
        # Ensure pool directory exists
        self.pool_dir.mkdir(parents=True, exist_ok=True)

    def is_allowed_type(self, filename: str, content_type: Optional[str] = None) -> bool:
        """
        Check if a file type is in the allowed list.

        Matches against configured MIME patterns (e.g., "image/*").
        Falls back to extension-based guessing if content_type is None.
        """
        mime = content_type or _guess_mime_type(filename)
        for pattern in self.allowed_types:
            if fnmatch(mime, pattern):
                return True
        return False

    async def download_discord_attachment(
        self,
        attachment: "discord.Attachment",  # type: ignore[name-defined]
        session_prefix: str = "",
    ) -> FileEntry:
        """
        Download a Discord attachment to the local pool.

        Args:
            attachment: discord.py Attachment object.
            session_prefix: Prefix for the file ID (typically first 8 chars of session ID).

        Returns:
            FileEntry for the downloaded file.

        Raises:
            ValueError: If the file exceeds max_size.
            Exception: If download fails.
        """
        # Check if single file exceeds pool size
        if attachment.size > self.max_size:
            raise ValueError(
                f"File {attachment.filename} ({attachment.size} bytes) exceeds "
                f"pool max size ({self.max_size} bytes)"
            )

        # Generate unique file ID
        uuid_short = uuid.uuid4().hex[:8]
        prefix = f"{session_prefix}_" if session_prefix else ""
        file_id = f"{prefix}{uuid_short}"

        # Sanitize and build local filename
        safe_name = _sanitize_filename(attachment.filename)
        local_filename = f"{file_id}_{safe_name}"
        local_path = self.pool_dir / local_filename

        # Determine MIME type
        mime_type = attachment.content_type or _guess_mime_type(attachment.filename)

        # Download file
        await attachment.save(local_path)

        # Create entry
        entry = FileEntry(
            file_id=file_id,
            original_name=attachment.filename,
            local_path=local_path,
            size=attachment.size,
            mime_type=mime_type,
            created_at=time.time(),
        )

        self._entries[file_id] = entry

        # Evict old files if over limit
        self._evict_if_needed()

        logger.info(
            f"Downloaded {attachment.filename} ({attachment.size} bytes) "
            f"as {file_id} to {local_path}"
        )

        return entry

    async def store_file(
        self,
        data: bytes,
        original_name: str,
        mime_type: str = "application/octet-stream",
        session_prefix: str = "",
    ) -> FileEntry:
        """
        Store raw bytes into the pool.

        Generic entry point for any platform adapter.
        """
        size = len(data)
        if size > self.max_size:
            raise ValueError(
                f"File {original_name} ({size} bytes) exceeds "
                f"pool max size ({self.max_size} bytes)"
            )

        uuid_short = uuid.uuid4().hex[:8]
        prefix = f"{session_prefix}_" if session_prefix else ""
        file_id = f"{prefix}{uuid_short}"

        safe_name = _sanitize_filename(original_name)
        local_filename = f"{file_id}_{safe_name}"
        local_path = self.pool_dir / local_filename

        local_path.write_bytes(data)

        entry = FileEntry(
            file_id=file_id,
            original_name=original_name,
            local_path=local_path,
            size=size,
            mime_type=mime_type,
            created_at=time.time(),
        )

        self._entries[file_id] = entry
        self._evict_if_needed()

        logger.info(
            f"Stored {original_name} ({size} bytes) "
            f"as {file_id} to {local_path}"
        )

        return entry

    async def store_from_path(
        self,
        source: Path,
        original_name: str,
        mime_type: str = "application/octet-stream",
        session_prefix: str = "",
    ) -> FileEntry:
        """
        Store a file from a local path into the pool (copies into pool dir).

        Generic entry point for any platform adapter.
        """
        import shutil

        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        size = source.stat().st_size
        if size > self.max_size:
            raise ValueError(
                f"File {original_name} ({size} bytes) exceeds "
                f"pool max size ({self.max_size} bytes)"
            )

        uuid_short = uuid.uuid4().hex[:8]
        prefix = f"{session_prefix}_" if session_prefix else ""
        file_id = f"{prefix}{uuid_short}"

        safe_name = _sanitize_filename(original_name)
        local_filename = f"{file_id}_{safe_name}"
        local_path = self.pool_dir / local_filename

        shutil.copy2(str(source), str(local_path))

        entry = FileEntry(
            file_id=file_id,
            original_name=original_name,
            local_path=local_path,
            size=size,
            mime_type=mime_type,
            created_at=time.time(),
        )

        self._entries[file_id] = entry
        self._evict_if_needed()

        logger.info(
            f"Stored {original_name} ({size} bytes) "
            f"as {file_id} to {local_path}"
        )

        return entry

    def add_file(
        self,
        local_path: Path,
        original_name: str,
        mime_type: str = "application/octet-stream",
        session_prefix: str = "",
    ) -> FileEntry:
        """
        Add an already-existing local file to the pool.

        This is useful for testing or for files that were obtained
        through means other than Discord attachment download.
        """
        if not local_path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        size = local_path.stat().st_size
        if size > self.max_size:
            raise ValueError(
                f"File {original_name} ({size} bytes) exceeds "
                f"pool max size ({self.max_size} bytes)"
            )

        uuid_short = uuid.uuid4().hex[:8]
        prefix = f"{session_prefix}_" if session_prefix else ""
        file_id = f"{prefix}{uuid_short}"

        entry = FileEntry(
            file_id=file_id,
            original_name=original_name,
            local_path=local_path,
            size=size,
            mime_type=mime_type,
            created_at=time.time(),
        )

        self._entries[file_id] = entry
        self._evict_if_needed()
        return entry

    def get_file(self, file_id: str) -> Optional[FileEntry]:
        """Retrieve a file entry by ID."""
        return self._entries.get(file_id)

    def _evict_if_needed(self) -> None:
        """Remove oldest files until total_size <= max_size."""
        while self.total_size > self.max_size and self._entries:
            # Find oldest entry
            oldest_id = min(
                self._entries, key=lambda fid: self._entries[fid].created_at
            )
            oldest = self._entries.pop(oldest_id)
            try:
                if oldest.local_path.exists():
                    oldest.local_path.unlink()
                logger.info(f"Evicted {oldest.file_id} ({oldest.size} bytes)")
            except OSError as e:
                logger.warning(f"Failed to delete evicted file {oldest.local_path}: {e}")

    @property
    def total_size(self) -> int:
        """Sum of all cached file sizes."""
        return sum(entry.size for entry in self._entries.values())

    @property
    def file_count(self) -> int:
        """Number of files in the pool."""
        return len(self._entries)
