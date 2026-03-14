"""Reply enhancer: parse agent responses for file attachments and quick-reply buttons.

Extracts two special syntaxes from agent reply text:

1. **File links** – Markdown links whose URL starts with ``file://``
   e.g. ``[screenshot](file:///tmp/shot.png)``

2. **Quick-reply buttons** – A ``---`` separator followed by
   ``[button text]`` tokens separated by ``|``
   e.g. ``---\\n[👌好的] | [✅提交PR] | [先review一下]``
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
    is_image: bool = False  # True when parsed from ![alt](file://...)


@dataclass
class QuickReplyButton:
    """A quick-reply button extracted from the trailing block."""

    text: str  # Button label / reply text (e.g. "👌好的" or "好的")


@dataclass
class EnhancedReply:
    """Result of processing an agent reply through the enhancer."""

    text: str  # Cleaned message text (file links & button block removed)
    files: List[FileLink] = field(default_factory=list)
    buttons: List[QuickReplyButton] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches markdown links with file:// URLs, including image links:
#   [label](file:///path)
#   ![alt](file:///path)
_FILE_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\((file://[^)]+)\)")

# Matches the quick-reply button block at the end of the text.
# A horizontal rule (``---``) on its own line, followed by one or more
# ``[text]`` tokens separated by ``|`` or full-width ``｜``.
_BUTTON_BLOCK_RE = re.compile(
    r"\n-{3,}\s*\n"  # --- separator line
    r"((?:\s*\[[^\]]+\]\s*(?:[|｜]\s*)?)+)"  # [text] tokens
    r"\s*$",  # trailing whitespace / end of string
)

# Individual button token:  [button text]
_BUTTON_TOKEN_RE = re.compile(r"\[([^\]]+)\]")


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
    for bang, label, url in _FILE_LINK_RE.findall(text):
        parsed = urlparse(url)
        if parsed.scheme != "file":
            continue
        path = unquote(parsed.path)
        if not os.path.isabs(path):
            logger.warning("Skipping non-absolute file link: %s", url)
            continue
        results.append(FileLink(label=label, path=path, is_image=(bang == "!")))
    return results


def _strip_file_links(text: str) -> str:
    """Replace ``[label](file://…)`` with just the label."""

    def _replacer(m: re.Match) -> str:
        label = m.group(2)
        url = m.group(3)
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
    for label in _BUTTON_TOKEN_RE.findall(block):
        label = label.strip()
        if label:
            buttons.append(QuickReplyButton(text=label))

    if not buttons:
        return [], text

    # Enforce a reasonable upper bound on button count
    buttons = buttons[:5]

    cleaned = text[: m.start()]
    return buttons, cleaned


# ---------------------------------------------------------------------------
# System prompt for injection into agent backends
# ---------------------------------------------------------------------------

REPLY_ENHANCEMENTS_PROMPT = """\
# Vibe Remote

Vibe Remote 是一个将 AI Agent 接入 Slack 等 IM 平台的中间层，\
用户正使用 IM 软件通过 Vibe Remote 的转发与你进行交互。

你有两个可选的回复增强能力：

## 1. 发送文件
用 Markdown 链接 + `file://` 协议即可将本地文件发送给用户：
示例：[文件1](file:///tmp/result.pdf)
Vibe Remote 会自动将文件作为附件发送。

### 图片语法
如果你希望按“图片附件”发送（而不是普通文件），请使用 Markdown 图片语法：
示例：![页面截图](file:///tmp/screenshot.jpg)

## 2. 快捷回复按钮
在消息最末尾，用 `---` 分隔线后跟 `[按钮文字]` 提供可点击的快捷回复，示例：
---
[👌 继续吧] | [✅ 提交PR] | [👀 先review一下]
规则：
- 根据对话上下文和用户习惯推测用户可能的回复意图，仅在确实对用户有帮助时添加
- 不要添加与用户下一步意图无关的废话，如：知道了、收到、谢谢
- 必须放在消息最末尾，`---` 分隔线之后
- 每个按钮用 `[文字]` 包裹，用 `|` 分隔，可用 emoji 开头优化表达
- 最多 2-4 个按钮，每个不超过 20 字符
"""
