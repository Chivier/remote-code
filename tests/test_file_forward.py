"""
Tests for the file forward module (head/file_forward.py).

Tests FILE_PATH_PATTERN regex, FileForwardMatcher path detection,
rule matching, and forward decision logic.
"""

import pytest
from head.file_forward import FILE_PATH_PATTERN, FileForwardMatcher, ForwardDecision
from head.config import FileForwardConfig, FileForwardRule

MB = 1024 * 1024


# ─── Helpers ───


def make_config(
    rules=None,
    default_max_size=5 * MB,
    default_auto=False,
    enabled=True,
):
    return FileForwardConfig(
        enabled=enabled,
        rules=rules or [],
        default_max_size=default_max_size,
        default_auto=default_auto,
    )


def make_matcher(rules=None, default_max_size=5 * MB, default_auto=False):
    cfg = make_config(rules=rules, default_max_size=default_max_size, default_auto=default_auto)
    return FileForwardMatcher(cfg)


# ─── TestFilePathDetection ───


class TestFilePathDetection:
    """Tests for FILE_PATH_PATTERN regex and detect_paths dedup."""

    def test_absolute_path_simple(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("See /home/user/report.pdf")]
        assert paths == ["/home/user/report.pdf"]

    def test_tilde_path(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("Created ~/docs/output.csv")]
        assert paths == ["~/docs/output.csv"]

    def test_nested_directory_path(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("/a/b/c/d/e/file.txt")]
        assert paths == ["/a/b/c/d/e/file.txt"]

    def test_various_extensions(self):
        text = "Files: /tmp/a.py /tmp/b.ts /tmp/c.json /tmp/d.yaml /tmp/e.png"
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer(text)]
        assert len(paths) == 5
        assert "/tmp/a.py" in paths
        assert "/tmp/e.png" in paths

    def test_path_with_dots_in_directory(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("/home/user/.config/app/settings.json")]
        assert paths == ["/home/user/.config/app/settings.json"]

    def test_path_with_hyphens_and_underscores(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("/var/log/my-app_v2/error.log")]
        assert paths == ["/var/log/my-app_v2/error.log"]

    def test_backtick_wrapped_path_excluded(self):
        """Paths inside backticks should be excluded by the lookbehind/lookahead."""
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("Run `cat /home/user/file.txt` to see")]
        assert paths == []

    def test_backtick_code_span_excluded(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("Use `/etc/config.yaml` for setup")]
        assert paths == []

    def test_path_without_extension_excluded(self):
        """Paths without file extensions should not match."""
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("See /usr/bin/python for details")]
        assert paths == []

    def test_relative_path_excluded(self):
        """Relative paths (not starting with / or ~/) should not match."""
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("See src/main.py for details")]
        assert paths == []

    def test_multiple_paths_in_text(self):
        text = "Compare /tmp/old.txt with /tmp/new.txt and ~/results/diff.patch"
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer(text)]
        assert len(paths) == 3

    def test_path_at_start_of_line(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("/etc/hosts.conf")]
        assert paths == ["/etc/hosts.conf"]

    def test_path_at_end_of_line(self):
        paths = [m.group(1) for m in FILE_PATH_PATTERN.finditer("Output saved to ~/out.log")]
        assert paths == ["~/out.log"]

    # ─── detect_paths dedup ───

    def test_detect_paths_returns_new_paths(self):
        matcher = make_matcher()
        paths = matcher.detect_paths("File at /tmp/a.py done", "ch1")
        assert paths == ["/tmp/a.py"]

    def test_detect_paths_dedup_within_same_channel(self):
        matcher = make_matcher()
        matcher.detect_paths("File at /tmp/a.py", "ch1")
        paths = matcher.detect_paths("Again /tmp/a.py and /tmp/b.py", "ch1")
        assert paths == ["/tmp/b.py"]

    def test_detect_paths_dedup_across_calls(self):
        matcher = make_matcher()
        matcher.detect_paths("/tmp/x.txt", "ch1")
        matcher.detect_paths("/tmp/y.txt", "ch1")
        paths = matcher.detect_paths("/tmp/x.txt /tmp/y.txt /tmp/z.txt", "ch1")
        assert paths == ["/tmp/z.txt"]

    def test_detect_paths_separate_channels(self):
        """Different channels have independent dedup state."""
        matcher = make_matcher()
        matcher.detect_paths("/tmp/a.py", "ch1")
        paths = matcher.detect_paths("/tmp/a.py", "ch2")
        assert paths == ["/tmp/a.py"]

    def test_reset_clears_dedup(self):
        matcher = make_matcher()
        matcher.detect_paths("/tmp/a.py", "ch1")
        matcher.reset("ch1")
        paths = matcher.detect_paths("/tmp/a.py", "ch1")
        assert paths == ["/tmp/a.py"]

    def test_cleanup_removes_channel_state(self):
        matcher = make_matcher()
        matcher.detect_paths("/tmp/a.py", "ch1")
        matcher.cleanup("ch1")
        paths = matcher.detect_paths("/tmp/a.py", "ch1")
        assert paths == ["/tmp/a.py"]

    def test_cleanup_nonexistent_channel_noop(self):
        matcher = make_matcher()
        matcher.cleanup("nonexistent")  # Should not raise

    def test_detect_paths_empty_text(self):
        matcher = make_matcher()
        assert matcher.detect_paths("", "ch1") == []

    def test_detect_paths_no_paths_in_text(self):
        matcher = make_matcher()
        assert matcher.detect_paths("Just some regular text with no file refs", "ch1") == []


# ─── TestRuleMatching ───


class TestRuleMatching:
    """Tests for match_rule: glob matching, first-match-wins, default fallback."""

    def test_exact_glob_match(self):
        rule = FileForwardRule(pattern="*.py", max_size=1 * MB, auto=True)
        matcher = make_matcher(rules=[rule])
        matched, is_default = matcher.match_rule("/home/user/script.py")
        assert matched.pattern == "*.py"
        assert is_default is False

    def test_first_match_wins(self):
        """When multiple rules match, the first one in the list wins."""
        rules = [
            FileForwardRule(pattern="*.py", max_size=1 * MB, auto=True),
            FileForwardRule(pattern="*.py", max_size=10 * MB, auto=False),
        ]
        matcher = make_matcher(rules=rules)
        matched, is_default = matcher.match_rule("/tmp/test.py")
        assert matched.max_size == 1 * MB
        assert matched.auto is True

    def test_first_match_wins_different_patterns(self):
        """More specific pattern listed first takes priority."""
        rules = [
            FileForwardRule(pattern="report.*", max_size=2 * MB, auto=True),
            FileForwardRule(pattern="*.pdf", max_size=10 * MB, auto=False),
        ]
        matcher = make_matcher(rules=rules)
        matched, _ = matcher.match_rule("/tmp/report.pdf")
        assert matched.pattern == "report.*"
        assert matched.max_size == 2 * MB

    def test_no_explicit_rule_falls_back_to_default(self):
        matcher = make_matcher(rules=[], default_max_size=3 * MB, default_auto=True)
        matched, is_default = matcher.match_rule("/tmp/file.xyz")
        assert is_default is True
        assert matched.max_size == 3 * MB
        assert matched.auto is True
        assert matched.pattern == "*"

    def test_default_fallback_uses_config_defaults(self):
        matcher = make_matcher(default_max_size=7 * MB, default_auto=False)
        matched, is_default = matcher.match_rule("/some/path/data.csv")
        assert is_default is True
        assert matched.max_size == 7 * MB
        assert matched.auto is False

    def test_non_matching_explicit_rules_fall_to_default(self):
        rules = [FileForwardRule(pattern="*.py", auto=True)]
        matcher = make_matcher(rules=rules, default_auto=False)
        matched, is_default = matcher.match_rule("/tmp/image.png")
        assert is_default is True
        assert matched.auto is False

    def test_wildcard_rule_matches_everything(self):
        rules = [FileForwardRule(pattern="*", max_size=2 * MB, auto=True)]
        matcher = make_matcher(rules=rules)
        matched, is_default = matcher.match_rule("/any/path/file.anything")
        assert is_default is False
        assert matched.pattern == "*"

    def test_match_uses_filename_only(self):
        """Matching uses only the filename, not the full path."""
        rules = [FileForwardRule(pattern="*.log", auto=True)]
        matcher = make_matcher(rules=rules)
        matched, is_default = matcher.match_rule("/var/log/very/deep/app.log")
        assert is_default is False
        assert matched.pattern == "*.log"

    def test_match_rule_bare_filename(self):
        """match_rule handles a bare filename (no directory component)."""
        rules = [FileForwardRule(pattern="*.txt", auto=True)]
        matcher = make_matcher(rules=rules)
        matched, is_default = matcher.match_rule("notes.txt")
        assert is_default is False


# ─── TestForwardDecision ───


class TestForwardDecision:
    """Tests for should_forward: action, reason, size checks."""

    def test_auto_send_when_auto_and_under_size(self):
        rules = [FileForwardRule(pattern="*.py", max_size=1 * MB, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/app.py", file_size=500)
        assert decision.action == "auto_send"
        assert decision.rule.pattern == "*.py"

    def test_notify_when_over_size(self):
        rules = [FileForwardRule(pattern="*.py", max_size=1 * MB, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/big.py", file_size=2 * MB)
        assert decision.action == "notify"
        assert "exceeds limit" in decision.reason

    def test_notify_when_auto_false(self):
        rules = [FileForwardRule(pattern="*.py", max_size=5 * MB, auto=False)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/app.py", file_size=100)
        assert decision.action == "notify"
        assert "notify only" in decision.reason

    def test_size_zero_skips_size_check(self):
        """file_size=0 is a pre-download intent check; size validation is skipped."""
        rules = [FileForwardRule(pattern="*.bin", max_size=1, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/huge.bin", file_size=0)
        assert decision.action == "auto_send"

    def test_default_rule_auto_send(self):
        matcher = make_matcher(default_auto=True, default_max_size=10 * MB)
        decision = matcher.should_forward("/tmp/any.xyz", file_size=100)
        assert decision.action == "auto_send"
        assert "default rule" in decision.reason

    def test_default_rule_notify(self):
        matcher = make_matcher(default_auto=False, default_max_size=10 * MB)
        decision = matcher.should_forward("/tmp/any.xyz", file_size=100)
        assert decision.action == "notify"
        assert "default rule" in decision.reason

    def test_explicit_rule_reason_contains_pattern(self):
        rules = [FileForwardRule(pattern="*.csv", auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/data/export.csv", file_size=100)
        assert "*.csv" in decision.reason

    def test_size_exactly_at_limit_allowed(self):
        """File exactly at max_size should be allowed (not exceeding)."""
        rules = [FileForwardRule(pattern="*", max_size=1000, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/file.dat", file_size=1000)
        assert decision.action == "auto_send"

    def test_size_one_over_limit_rejected(self):
        rules = [FileForwardRule(pattern="*", max_size=1000, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/file.dat", file_size=1001)
        assert decision.action == "notify"
        assert "exceeds limit" in decision.reason

    def test_max_size_zero_disables_size_check(self):
        """max_size=0 means no size limit (the condition file_size > rule.max_size
        is gated by rule.max_size > 0)."""
        rules = [FileForwardRule(pattern="*", max_size=0, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/huge.dat", file_size=999 * MB)
        assert decision.action == "auto_send"

    def test_decision_has_correct_rule_reference(self):
        rule = FileForwardRule(pattern="*.log", max_size=2 * MB, auto=True)
        matcher = make_matcher(rules=[rule])
        decision = matcher.should_forward("/var/app.log", file_size=100)
        assert decision.rule is rule

    def test_decision_default_rule_reference(self):
        matcher = make_matcher(default_max_size=5 * MB, default_auto=False)
        decision = matcher.should_forward("/tmp/x.txt", file_size=100)
        assert decision.rule is not None
        assert decision.rule.pattern == "*"

    def test_notify_reason_includes_size_info(self):
        rules = [FileForwardRule(pattern="*", max_size=1 * MB, auto=True)]
        matcher = make_matcher(rules=rules)
        decision = matcher.should_forward("/tmp/big.dat", file_size=3 * MB)
        assert "3.0MB" in decision.reason
        assert "1.0MB" in decision.reason


# ─── TestForwardConfig ───


class TestForwardConfig:
    """Tests for config construction and edge cases."""

    def test_default_config_values(self):
        cfg = FileForwardConfig()
        assert cfg.enabled is False
        assert cfg.rules == []
        assert cfg.default_max_size == 5 * MB
        assert cfg.default_auto is False
        assert cfg.download_dir == "~/.codecast/downloads"

    def test_config_with_rules(self):
        rules = [
            FileForwardRule(pattern="*.py", max_size=1 * MB, auto=True),
            FileForwardRule(pattern="*.log", max_size=10 * MB, auto=False),
        ]
        cfg = FileForwardConfig(enabled=True, rules=rules)
        assert cfg.enabled is True
        assert len(cfg.rules) == 2
        assert cfg.rules[0].pattern == "*.py"

    def test_rule_default_values(self):
        rule = FileForwardRule(pattern="*.txt")
        assert rule.max_size == 5 * MB
        assert rule.auto is False

    def test_matcher_with_empty_config(self):
        cfg = FileForwardConfig()
        matcher = FileForwardMatcher(cfg)
        decision = matcher.should_forward("/tmp/file.txt", file_size=100)
        assert decision.action == "notify"  # default_auto is False

    def test_matcher_with_disabled_config_still_works(self):
        """FileForwardMatcher itself does not check enabled; that's the caller's job."""
        cfg = FileForwardConfig(enabled=False, default_auto=True)
        matcher = FileForwardMatcher(cfg)
        decision = matcher.should_forward("/tmp/file.txt", file_size=100)
        assert decision.action == "auto_send"

    def test_forward_decision_dataclass(self):
        d = ForwardDecision(action="auto_send", reason="test reason", rule=None)
        assert d.action == "auto_send"
        assert d.reason == "test reason"
        assert d.rule is None

    def test_config_custom_download_dir(self):
        cfg = FileForwardConfig(download_dir="/custom/path")
        assert cfg.download_dir == "/custom/path"
