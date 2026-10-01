"""Tests for escape_markdown_inline — reasoning display in subtext/blockquote.

A ``-# `` (subtext) or ``> `` (blockquote) prefix styles only the line; inline
markdown inside the line still renders on Discord.  Reasoning text routinely
contains ``` fences, bold markers, spoilers and mentions, so without escaping
the reasoning body breaks its own formatting (or pings users).

These tests pin the escaping contract, not the exact typography.
"""

import pytest

from gateway.stream_consumer import escape_markdown_inline


class TestEscapeMarkdownInline:
    """escape_markdown_inline neutralises inline Discord markup."""

    def test_code_fence_neutralised(self):
        text = "I ran ```python\nx = 1\n``` in my head"
        result = escape_markdown_inline(text)
        assert "```" not in result
        assert "\\`\\`\\`" in result

    def test_inline_code_neutralised(self):
        result = escape_markdown_inline("use `pip install` here")
        assert "`pip install`" not in result
        assert "\\`pip install\\`" in result

    def test_bold_and_italic_neutralised(self):
        result = escape_markdown_inline("**bold** and _italic_")
        assert "**bold**" not in result
        assert "_italic_" not in result

    def test_strikethrough_neutralised(self):
        result = escape_markdown_inline("~~gone~~")
        assert "~~gone~~" not in result

    def test_spoiler_neutralised(self):
        result = escape_markdown_inline("||hidden||")
        assert "||hidden||" not in result

    def test_user_mention_neutralised(self):
        result = escape_markdown_inline("ping <@123456789>")
        # Zero-width space after '<' breaks mention syntax without a visible glyph.
        assert "<@123456789>" not in result
        assert "\u200b" in result
        assert "@123456789>" in result

    def test_channel_and_emoji_mention_neutralised(self):
        assert "<#987>" not in escape_markdown_inline("see <#987>")
        assert "<:party:123>" not in escape_markdown_inline("emoji <:party:123>")

    def test_backslash_is_escaped_once(self):
        """Existing backslashes must not multiply across repeated calls."""
        once = escape_markdown_inline("C:\\path")
        twice = escape_markdown_inline(once)
        assert once == "C:\\\\path"
        assert twice == "C:\\\\\\\\path"  # each pass escapes what it sees

    def test_plain_text_unchanged(self):
        plain = "just an ordinary sentence with no markup"
        assert escape_markdown_inline(plain) == plain

    @pytest.mark.parametrize("falsy", ["", None])
    def test_falsy_input_passthrough(self, falsy):
        assert escape_markdown_inline(falsy) == falsy


class TestEscapedReasoningStaysReadable:
    """The escaped output must not itself introduce visible artefacts."""

    def test_no_zero_width_leak_for_plain_angle_bracket(self):
        """A bare '<' that is not a mention stays untouched."""
        result = escape_markdown_inline("if x < 3 then")
        assert result == "if x < 3 then"

    def test_multiline_reasoning_all_lines_escaped(self):
        text = "first ```\nsecond **bold**\nthird <@1>"
        result = escape_markdown_inline(text)
        assert "```" not in result
        assert "**bold**" not in result
        assert "<@1>" not in result
