"""
File Forward - detects file paths in Claude responses and manages forwarding decisions.

When Claude CLI generates or references files on the remote machine, this module
detects those file paths, checks them against configurable rules, and decides
whether to auto-send, notify, or skip.
"""

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Optional

from .config import FileForwardConfig, FileForwardRule

logger = logging.getLogger(__name__)

# Match absolute paths (/...) and tilde paths (~/...) with file extensions.
# Excludes paths inside backtick code spans and paths that are part of longer words.
FILE_PATH_PATTERN = re.compile(
    r'(?<![`\w])((?:/|~/)(?:[\w.~-]+/)*[\w.-]+\.(\w+))(?![`\w])'
)


@dataclass
class ForwardDecision:
    """Result of evaluating whether a file should be forwarded."""

    action: str  # "auto_send", "notify", "skip"
    reason: str  # Human-readable reason
    rule: Optional[FileForwardRule]


class FileForwardMatcher:
    """Matches file paths against configured forwarding rules."""

    def __init__(self, config: FileForwardConfig):
        self.config = config
        # Per-channel dedup state
        self._forwarded: dict[str, set[str]] = {}

    def reset(self, channel_id: str) -> None:
        """Reset dedup tracker for a channel (call at start of each stream)."""
        self._forwarded[channel_id] = set()

    def cleanup(self, channel_id: str) -> None:
        """Remove dedup state for a channel (call when stream ends)."""
        self._forwarded.pop(channel_id, None)

    def detect_paths(self, text: str, channel_id: str) -> list[str]:
        """Extract file paths from text, deduplicating within the channel stream.

        Returns list of new (not yet forwarded) file paths found in text.
        """
        seen = self._forwarded.get(channel_id, set())
        paths = []
        for match in FILE_PATH_PATTERN.finditer(text):
            path = match.group(1)
            if path not in seen:
                paths.append(path)
                seen.add(path)
        self._forwarded[channel_id] = seen
        return paths

    def match_rule(self, path: str) -> tuple[Optional[FileForwardRule], bool]:
        """Find the best matching rule for a path.

        Returns:
            (rule, is_default) where:
            - rule: matching FileForwardRule, or a synthetic default rule
            - is_default: True if no explicit rule matched (using defaults)
            Returns (None, True) if no rule matches and defaults don't apply.
        """
        filename = path.rsplit("/", 1)[-1] if "/" in path else path

        # Check explicit rules in order (first match wins)
        for rule in self.config.rules:
            if fnmatch.fnmatch(filename, rule.pattern):
                return rule, False

        # Fall back to defaults — create a synthetic rule
        default_rule = FileForwardRule(
            pattern="*",
            max_size=self.config.default_max_size,
            auto=self.config.default_auto,
        )
        return default_rule, True

    def should_forward(self, path: str, file_size: int) -> ForwardDecision:
        """Decide what to do with a detected file.

        Args:
            path: The detected file path.
            file_size: Actual file size in bytes. Pass 0 for pre-download
                       intent check (skips size validation).

        Returns:
            ForwardDecision with action and reason.
        """
        rule, is_default = self.match_rule(path)

        if rule is None:
            return ForwardDecision(
                action="skip",
                reason="No matching rule.",
                rule=None,
            )

        # Size check (only when file_size > 0, i.e., post-download)
        if file_size > 0 and rule.max_size > 0 and file_size > rule.max_size:
            size_mb = file_size / (1024 * 1024)
            limit_mb = rule.max_size / (1024 * 1024)
            return ForwardDecision(
                action="notify",
                reason=f"File size ({size_mb:.1f}MB) exceeds limit ({limit_mb:.1f}MB).",
                rule=rule,
            )

        if rule.auto:
            source = "default rule" if is_default else f"rule '{rule.pattern}'"
            return ForwardDecision(
                action="auto_send",
                reason=f"Matched {source} (auto-send).",
                rule=rule,
            )
        else:
            source = "default rule" if is_default else f"rule '{rule.pattern}'"
            return ForwardDecision(
                action="notify",
                reason=f"Matched {source} (notify only).",
                rule=rule,
            )
