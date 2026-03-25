"""Telegram formatter.

Telegram formatting is strict and easy to break when escaping is incomplete.
Use a conservative plain-text formatter for now; richer formatting can be
added later once the adapter has stronger test coverage.
"""

from .base_formatter import BaseMarkdownFormatter


class TelegramFormatter(BaseMarkdownFormatter):
    def format_bold(self, text: str) -> str:
        return text

    def format_italic(self, text: str) -> str:
        return text

    def format_strikethrough(self, text: str) -> str:
        return text

    def format_link(self, text: str, url: str) -> str:
        return f"{text} ({url})"

    def escape_special_chars(self, text: str) -> str:
        return text
