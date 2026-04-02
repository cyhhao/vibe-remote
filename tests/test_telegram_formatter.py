from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im.formatters.telegram_formatter import TelegramFormatter


def test_render_preserves_html_escaped_link_query_params() -> None:
    formatter = TelegramFormatter()

    rendered = formatter.render("[Docs](https://example.com?q=hello&lang=en)")

    assert rendered == '<a href="https://example.com?q=hello&amp;lang=en">Docs</a>'
