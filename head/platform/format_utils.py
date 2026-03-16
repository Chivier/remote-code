"""
Platform-specific formatting utilities.

Provides conversion functions for adapting markdown-style content
to platform-specific formats (e.g., Telegram HTML).
"""

import re
from html import escape as html_escape


def markdown_to_telegram_html(text: str) -> str:
    """
    Convert simple markdown to Telegram-compatible HTML.

    Handles: **bold**, *italic*, `code`, ```code blocks```, ~~strikethrough~~

    Falls back to plain text (with HTML entities escaped) on any error.
    """
    try:
        result = html_escape(text)

        # Code blocks (``` ... ```) -> <pre>...</pre>
        # Must be done before inline code
        def replace_code_block(m: re.Match) -> str:
            lang = m.group(1) or ""
            code = m.group(2)
            # Unescape HTML entities inside code blocks since we already escaped
            return f"<pre>{code}</pre>"

        result = re.sub(
            r"```(\w*)\n?(.*?)```",
            replace_code_block,
            result,
            flags=re.DOTALL,
        )

        # Inline code (`...`) -> <code>...</code>
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Bold (**...**) -> <b>...</b>
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

        # Italic (*...*) -> <i>...</i>
        # Be careful not to match ** which is bold
        result = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", result)

        # Strikethrough (~~...~~) -> <s>...</s>
        result = re.sub(r"~~(.+?)~~", r"<s>\1</s>", result)

        return result
    except Exception:
        # On any formatting error, return HTML-escaped plain text
        return html_escape(text)
