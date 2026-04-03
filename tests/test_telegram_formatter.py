from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im.formatters.telegram_formatter import TelegramFormatter


def test_render_preserves_html_escaped_link_query_params() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("[Docs](https://example.com?q=hello&lang=en)")

    assert rendered == '<a href="https://example.com?q=hello&amp;lang=en">Docs</a>'


def test_render_preserves_parentheses_in_markdown_link_urls() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("[Wiki](https://en.wikipedia.org/wiki/Function_(mathematics))")

    assert rendered == '<a href="https://en.wikipedia.org/wiki/Function_(mathematics)">Wiki</a>'


def test_render_does_not_join_plain_brackets_with_later_link() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("test [x] and [Docs](https://a.com)")

    assert rendered == 'test [x] and <a href="https://a.com">Docs</a>'


def test_render_preserves_markdown_metacharacters_inside_link_href() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("[x](https://a.com?q=a*b*)")

    assert rendered == '<a href="https://a.com?q=a*b*">x</a>'


def test_render_preserves_literal_placeholder_like_text() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("literal @@TG0@@ and `code`")

    assert rendered == "literal @@TG0@@ and <code>code</code>"


def test_render_preserves_valid_nested_emphasis_structure() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("**bold *italic***")

    assert rendered == "<b>bold <i>italic</i></b>"
