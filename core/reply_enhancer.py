"""Reply enhancer: parse agent responses for file attachments and quick-reply buttons.

Extracts two special syntaxes from agent reply text:

1. **File links** – Markdown links whose URL starts with ``file://``
   e.g. ``[screenshot](file:///tmp/shot.png)``

2. **Quick-reply buttons** – A ``---`` separator followed by
   ``[:emoji:label]`` tokens separated by ``|``
   e.g. ``---\\n[:ok_hand:好的] | [:white_check_mark:提PR吧]``
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FileLink:
    """A file reference extracted from agent reply text."""

    label: str  # Markdown link text (e.g. "screenshot")
    path: str  # Absolute local path (e.g. "/tmp/shot.png")


@dataclass
class QuickReplyButton:
    """A quick-reply button extracted from the trailing block."""

    emoji: str  # Slack emoji name without colons (e.g. "ok_hand")
    text: str  # Button label / reply text (e.g. "好的")


@dataclass
class EnhancedReply:
    """Result of processing an agent reply through the enhancer."""

    text: str  # Cleaned message text (file links & button block removed)
    files: List[FileLink] = field(default_factory=list)
    buttons: List[QuickReplyButton] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches markdown links with file:// URLs:  [label](file:///path)
_FILE_LINK_RE = re.compile(r"\[([^\]]*)\]\((file://[^)]+)\)")

# Matches the quick-reply button block at the end of the text.
# A horizontal rule (``---``) on its own line, followed by one or more
# ``[:emoji:text]`` tokens separated by ``|`` or full-width ``｜``.
# We allow optional whitespace and tolerance for the Chinese full-width pipe.
_BUTTON_BLOCK_RE = re.compile(
    r"\n-{3,}\s*\n"  # --- separator line
    r"((?:\s*\[:[\w+-]+:\s*[^\]]*\]\s*(?:[|｜]\s*)?)+)"  # button tokens
    r"\s*$",  # trailing whitespace / end of string
)

# Individual button token:  [:emoji_name:button text]
_BUTTON_TOKEN_RE = re.compile(r"\[:([\w+-]+):\s*([^\]]*)\]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_reply(text: str) -> EnhancedReply:
    """Parse *text* and return an ``EnhancedReply``.

    The returned ``.text`` has file-link markup converted to plain labels and
    the trailing button block (if any) stripped.
    """
    files = _extract_file_links(text)
    text_no_files = _strip_file_links(text) if files else text
    buttons, text_clean = _extract_buttons(text_no_files)
    return EnhancedReply(text=text_clean.rstrip(), files=files, buttons=buttons)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_file_links(text: str) -> List[FileLink]:
    """Return all ``FileLink`` instances found in *text*."""
    results: List[FileLink] = []
    for label, url in _FILE_LINK_RE.findall(text):
        parsed = urlparse(url)
        if parsed.scheme != "file":
            continue
        path = unquote(parsed.path)
        if not os.path.isabs(path):
            logger.warning("Skipping non-absolute file link: %s", url)
            continue
        results.append(FileLink(label=label, path=path))
    return results


def _strip_file_links(text: str) -> str:
    """Replace ``[label](file://…)`` with just the label."""

    def _replacer(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        if url.startswith("file://"):
            return label  # keep the label text, drop the link
        return m.group(0)

    return _FILE_LINK_RE.sub(_replacer, text)


def _extract_buttons(text: str) -> Tuple[List[QuickReplyButton], str]:
    """Extract trailing quick-reply buttons and return ``(buttons, cleaned_text)``."""
    m = _BUTTON_BLOCK_RE.search(text)
    if not m:
        return [], text

    block = m.group(1)
    buttons: List[QuickReplyButton] = []
    for emoji, label in _BUTTON_TOKEN_RE.findall(block):
        label = label.strip()
        if label:
            buttons.append(QuickReplyButton(emoji=emoji, text=label))

    if not buttons:
        return [], text

    cleaned = text[: m.start()]
    return buttons, cleaned


# ---------------------------------------------------------------------------
# System prompt for injection into agent backends
# ---------------------------------------------------------------------------

REPLY_ENHANCEMENTS_PROMPT = """\
## Vibe Remote Enhanced Reply

You have two optional reply capabilities:

### 1. Send Files
To send a local file to the user, use a Markdown link with `file://` protocol:
[description](file:///absolute/path/to/file)
Example: [screenshot](file:///tmp/result.png)
The system will auto-attach the file. Use this for images, logs, diffs, etc.

### 2. Quick Reply Buttons
To offer the user clickable shortcut buttons, append a `---` block at the very end of your message:
---
[:emoji_name:button text] | [:emoji_name:button text]
Example:
---
[:ok_hand:好的] | [:white_check_mark:提交PR] | [:mag:先review一下]
Rules:
- Must be the last content in your message, after a `---` line.
- Each button: `[:slack_emoji_name:short text]`, separated by `|`.
- Infer likely user replies from conversation context; only add when genuinely helpful.
- Keep 2-4 buttons max, each label under 20 chars.\
"""
