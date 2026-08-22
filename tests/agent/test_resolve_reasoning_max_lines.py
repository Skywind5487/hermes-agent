"""Tests for resolve_reasoning_max_lines and _truncate_reasoning_lines production helpers.

This exercises the production seams in agent/display.py that both
CLI and gateway callers must use for reasoning_max_lines resolution.
Tests cover: unset defaults, invalid fallback, positive override,
0 = unlimited, boolean rejection, negative fallback, reasoning_full
precedence, and code-fence/backtick regression.
"""

import sys

import pytest

from agent.display import resolve_reasoning_max_lines, _truncate_reasoning_lines
from gateway.stream_consumer import escape_code_fences_for_display


class TestResolveReasoningMaxLines:
    """Test the production helper for reasoning_max_lines resolution."""

    def test_unset_returns_surface_default(self):
        """Key absent → surface_default (5/10/15 per surface)."""
        config = {"display": {}}
        assert resolve_reasoning_max_lines(config, 5) == 5
        assert resolve_reasoning_max_lines(config, 10) == 10
        assert resolve_reasoning_max_lines(config, 15) == 15

    def test_none_config_returns_surface_default(self):
        """None config → surface_default."""
        assert resolve_reasoning_max_lines(None, 5) == 5
        assert resolve_reasoning_max_lines(None, 10) == 10

    def test_empty_config_returns_surface_default(self):
        """Empty dict → surface_default."""
        assert resolve_reasoning_max_lines({}, 5) == 5

    def test_missing_display_key_returns_surface_default(self):
        """display key missing → surface_default."""
        config = {"other": "value"}
        assert resolve_reasoning_max_lines(config, 10) == 10

    def test_none_value_returns_surface_default(self):
        """reasoning_max_lines: null → surface_default."""
        config = {"display": {"reasoning_max_lines": None}}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_positive_int_override(self):
        """Positive integer → that cap."""
        config = {"display": {"reasoning_max_lines": 20}}
        assert resolve_reasoning_max_lines(config, 5) == 20
        assert resolve_reasoning_max_lines(config, 10) == 20
        assert resolve_reasoning_max_lines(config, 15) == 20

    def test_zero_means_unlimited(self):
        """Explicit 0 → unlimited (sys.maxsize)."""
        config = {"display": {"reasoning_max_lines": 0}}
        assert resolve_reasoning_max_lines(config, 5) == sys.maxsize
        assert resolve_reasoning_max_lines(config, 10) == sys.maxsize
        assert resolve_reasoning_max_lines(config, 15) == sys.maxsize

    def test_negative_falls_back_to_surface_default(self):
        """Negative value → surface_default."""
        config = {"display": {"reasoning_max_lines": -5}}
        assert resolve_reasoning_max_lines(config, 5) == 5
        assert resolve_reasoning_max_lines(config, 10) == 10

    def test_boolean_true_falls_back_to_surface_default(self):
        """Boolean True → surface_default (bool is subclass of int)."""
        config = {"display": {"reasoning_max_lines": True}}
        assert resolve_reasoning_max_lines(config, 5) == 5
        assert resolve_reasoning_max_lines(config, 10) == 10

    def test_boolean_false_falls_back_to_surface_default(self):
        """Boolean False → surface_default."""
        config = {"display": {"reasoning_max_lines": False}}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_string_integer_accepted(self):
        """Integer-like string → that cap."""
        config = {"display": {"reasoning_max_lines": "25"}}
        assert resolve_reasoning_max_lines(config, 5) == 25

    def test_string_zero_means_unlimited(self):
        """String "0" → unlimited."""
        config = {"display": {"reasoning_max_lines": "0"}}
        assert resolve_reasoning_max_lines(config, 10) == sys.maxsize

    def test_invalid_string_falls_back_to_surface_default(self):
        """Non-numeric string → surface_default."""
        config = {"display": {"reasoning_max_lines": "unlimited"}}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_float_falls_back_to_surface_default(self):
        """Float → surface_default (not int)."""
        config = {"display": {"reasoning_max_lines": 5.5}}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_list_falls_back_to_surface_default(self):
        """List → surface_default."""
        config = {"display": {"reasoning_max_lines": [10]}}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_display_not_dict_falls_back_to_surface_default(self):
        """display key is not a dict → surface_default."""
        config = {"display": "invalid"}
        assert resolve_reasoning_max_lines(config, 5) == 5

    def test_config_not_dict_falls_back_to_surface_default(self):
        """Config is not a dict → surface_default."""
        assert resolve_reasoning_max_lines("invalid", 5) == 5
        assert resolve_reasoning_max_lines(123, 5) == 5

    def test_one_is_valid_cap(self):
        """Positive integer 1 → 1."""
        config = {"display": {"reasoning_max_lines": 1}}
        assert resolve_reasoning_max_lines(config, 5) == 1

    def test_large_positive_cap(self):
        """Large positive integer → that cap."""
        config = {"display": {"reasoning_max_lines": 1000}}
        assert resolve_reasoning_max_lines(config, 5) == 1000


class TestTruncateReasoningLines:
    """Test the _truncate_reasoning_lines production helper."""

    def test_short_text_unchanged(self):
        """Text within limit → returned unchanged."""
        text = "line1\nline2\nline3"
        result = _truncate_reasoning_lines(text, 5, "\n... ({n} more lines)")
        assert result == text

    def test_exact_limit_unchanged(self):
        """Text exactly at limit → returned unchanged."""
        text = "line1\nline2\nline3\nline4\nline5"
        result = _truncate_reasoning_lines(text, 5, "\n... ({n} more lines)")
        assert result == text

    def test_long_text_truncated(self):
        """Text exceeding limit → truncated with suffix."""
        text = "\n".join(f"line{i}" for i in range(10))
        result = _truncate_reasoning_lines(text, 5, "\n... ({n} more lines)")
        assert result == "line0\nline1\nline2\nline3\nline4\n... (5 more lines)"

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace → stripped."""
        text = "  line1\nline2  \n"
        result = _truncate_reasoning_lines(text, 5, "\n... ({n} more lines)")
        assert result == "line1\nline2"

    def test_empty_text(self):
        """Empty text → returned as empty string."""
        result = _truncate_reasoning_lines("", 5, "\n... ({n} more lines)")
        assert result == ""

    def test_custom_suffix_template(self):
        """Custom suffix template → used correctly."""
        text = "\n".join(f"line{i}" for i in range(10))
        result = _truncate_reasoning_lines(text, 3, " [showing 3 of {n} more]")
        assert result == "line0\nline1\nline2 [showing 3 of 7 more]"


class _RecapStub:
    """Minimal carrier for the CLI recap production seam.

    Attributes are resolved through the production path (CLI init resolves
    ``reasoning_max_lines_recap`` from config via ``resolve_reasoning_max_lines``
    with the legacy 10-line default), and the recap decision is the real
    ``HermesCLI._build_reasoning_recap`` method — so these tests exercise the
    actual CLI renderer decision, not a local reimplementation.
    """

    def __init__(self, config, reasoning_full=False):
        self.reasoning_full = reasoning_full
        self.reasoning_max_lines_recap = resolve_reasoning_max_lines(config, 10)
        import cli
        self._build_reasoning_recap = cli.HermesCLI._build_reasoning_recap.__get__(self)


class TestReasoningFullPrecedence:
    """Test reasoning_full precedence over reasoning_max_lines via the CLI seam."""

    def test_reasoning_full_overrides_max_lines(self):
        """reasoning_full=True → reasoning_max_lines ignored (full recap)."""
        reasoning = "\n".join(f"line{i}" for i in range(20))
        stub = _RecapStub(
            {"display": {"reasoning_max_lines": 10}}, reasoning_full=True
        )
        display_reasoning = stub._build_reasoning_recap(reasoning)
        # With reasoning_full=True, all 20 lines are shown uncollapsed.
        assert len(display_reasoning.splitlines()) == 20

    def test_reasoning_max_lines_when_full_false(self):
        """reasoning_full=False → reasoning_max_lines applied to recap."""
        reasoning = "\n".join(f"line{i}" for i in range(20))
        stub = _RecapStub(
            {"display": {"reasoning_max_lines": 10}}, reasoning_full=False
        )
        display_reasoning = stub._build_reasoning_recap(reasoning)
        # Truncated to 10 content lines + 1 suffix line (with /reasoning full hint).
        lines = display_reasoning.splitlines()
        assert len(lines) == 11
        assert "... (10 more lines — /reasoning full to show)" in lines[-1]

    def test_recap_defaults_to_10_when_unset(self):
        """Key absent → CLI recap keeps the legacy 10-line default."""
        reasoning = "\n".join(f"line{i}" for i in range(20))
        stub = _RecapStub({}, reasoning_full=False)
        display_reasoning = stub._build_reasoning_recap(reasoning)
        lines = display_reasoning.splitlines()
        assert len(lines) == 11  # 10 content lines + suffix
        assert "... (10 more lines — /reasoning full to show)" in lines[-1]

    def test_recap_unlimited_when_explicit_zero(self):
        """Explicit 0 → recap is unlimited even with reasoning_full=False."""
        reasoning = "\n".join(f"line{i}" for i in range(20))
        stub = _RecapStub(
            {"display": {"reasoning_max_lines": 0}}, reasoning_full=False
        )
        display_reasoning = stub._build_reasoning_recap(reasoning)
        assert len(display_reasoning.splitlines()) == 20


class TestCodeFenceRegression:
    """Test code-fence/backtick escaping after truncation."""

    def test_truncation_then_escape(self):
        """Truncate first, then escape code fences."""
        # Reasoning with code fences
        reasoning = "\n".join([
            "Thinking about code:",
            "```python",
            "def foo():",
            "    pass",
            "```",
            "More thinking:",
            "```javascript",
            "function bar() {}",
            "```",
            "Final thoughts",
        ])

        # Truncate to 8 lines (includes both code blocks)
        truncated = _truncate_reasoning_lines(reasoning, 8, "\n... ({n} more lines)")

        # Then escape for display
        escaped = escape_code_fences_for_display(truncated)

        # Verify truncation happened
        assert len(truncated.splitlines()) == 9  # 8 + suffix

        # Verify code fences were escaped
        assert "\\`\\`\\`python" in escaped
        assert "\\`\\`\\`javascript" in escaped

    def test_truncation_preserves_fence_balance(self):
        """Truncation doesn't break fence balance in unexpected ways."""
        reasoning = "\n".join([
            "```",
            "code block",
            "```",
            "more text",
            "```",
            "another block",
            "```",
        ])

        truncated = _truncate_reasoning_lines(reasoning, 4, "\n... ({n} more lines)")
        escaped = escape_code_fences_for_display(truncated)

        # Should have escaped all fences in the truncated output
        # First 4 lines: ```, code block, ```, more text
        # So 2 fences should be escaped
        assert escaped.count("\\`\\`\\`") == 2

    def test_backtick_not_escaped(self):
        """Single backticks are not escaped (only triple backticks)."""
        reasoning = "Use `code` inline and ```block``` for code"
        escaped = escape_code_fences_for_display(reasoning)

        # Single backticks preserved
        assert "`code`" in escaped
        # Triple backticks escaped
        assert "\\`\\`\\`block\\`\\`\\`" in escaped



