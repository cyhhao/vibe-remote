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

    @staticmethod
    def _find_link_url_end(text: str, start: int) -> int | None:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char.isspace():
                return None
            if char == "(":
                depth += 1
                continue
            if char != ")":
                continue
            if depth == 0:
                return index
            depth -= 1
        return None

    @staticmethod
    def _find_link_label_end(text: str, start: int) -> int | None:
        for index in range(start + 1, len(text)):
            char = text[index]
            if char == "[":
                return None
            if char == "]" and index + 1 < len(text) and text[index + 1] == "(":
                return index
            if char == "]":
                return None
        return None

    def _render_links(self, text: str) -> str:
        rendered_parts: list[str] = []
        cursor = 0

        while cursor < len(text):
            start = text.find("[", cursor)
            if start < 0:
                rendered_parts.append(text[cursor:])
                break

            label_end = self._find_link_label_end(text, start)
            if label_end is None:
                rendered_parts.append(text[cursor : start + 1])
                cursor = start + 1
                continue

            url_start = label_end + 2
            if not text.startswith(("http://", "https://"), url_start):
                rendered_parts.append(text[cursor : start + 1])
                cursor = start + 1
                continue

            url_end = self._find_link_url_end(text, url_start)
            if url_end is None:
                rendered_parts.append(text[cursor : start + 1])
                cursor = start + 1
                continue

            rendered_parts.append(text[cursor:start])
            rendered_parts.append(f'<a href="{text[url_start:url_end]}">{text[start + 1:label_end]}</a>')
            cursor = url_end + 1

        return "".join(rendered_parts)

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

        rendered = self._render_links(rendered)
        rendered = self._BOLD_RE.sub(r"<b>\1</b>", rendered)
        rendered = self._STRIKE_RE.sub(r"<s>\1</s>", rendered)
        rendered = self._ITALIC_RE.sub(r"<i>\1</i>", rendered)

        for token, replacement in placeholders.items():
            rendered = rendered.replace(token, replacement)
        return rendered
