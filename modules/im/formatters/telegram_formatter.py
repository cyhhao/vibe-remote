"""Telegram formatter with HTML rendering.

The rest of the app emits standard Markdown-ish text (`**bold**`, backticks,
links). Telegram is stricter than Slack/Discord, so we normalize that subset
into Bot API HTML before sending.
"""

from __future__ import annotations

import html
import re

from .base_formatter import BaseMarkdownFormatter


class TelegramFormatter(BaseMarkdownFormatter):
    _CODE_BLOCK_RE = re.compile(r"```(?:([\w.+-]+)\n)?(.*?)```", re.DOTALL)
    _INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
    _LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    _BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
    _ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
    _STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)

    def format_bold(self, text: str) -> str:
        return f"**{text}**"

    def format_italic(self, text: str) -> str:
        return f"*{text}*"

    def format_strikethrough(self, text: str) -> str:
        return f"~~{text}~~"

    def format_link(self, text: str, url: str) -> str:
        return f"[{text}]({url})"

    def escape_special_chars(self, text: str) -> str:
        return text

    def render(self, text: str) -> str:
        if not text:
            return ""

        placeholders: dict[str, str] = {}

        def stash(replacement: str) -> str:
            token = f"@@TG{len(placeholders)}@@"
            placeholders[token] = replacement
            return token

        def render_code_block(match: re.Match[str]) -> str:
            language = (match.group(1) or "").strip()
            code = html.escape(match.group(2).strip("\n"))
            if language:
                return f'<pre><code class="language-{html.escape(language, quote=True)}">{code}</code></pre>'
            return f"<pre><code>{code}</code></pre>"

        def render_inline_code(match: re.Match[str]) -> str:
            return f"<code>{html.escape(match.group(1))}</code>"

        rendered = self._CODE_BLOCK_RE.sub(lambda m: stash(render_code_block(m)), text)
        rendered = self._INLINE_CODE_RE.sub(lambda m: stash(render_inline_code(m)), rendered)
        rendered = html.escape(rendered)

        rendered = self._LINK_RE.sub(
            lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
            rendered,
        )
        rendered = self._BOLD_RE.sub(r"<b>\1</b>", rendered)
        rendered = self._STRIKE_RE.sub(r"<s>\1</s>", rendered)
        rendered = self._ITALIC_RE.sub(r"<i>\1</i>", rendered)

        for token, replacement in placeholders.items():
            rendered = rendered.replace(token, replacement)
        return rendered
