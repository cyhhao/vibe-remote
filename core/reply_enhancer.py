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
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

from modules.im import MessageContext

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
_FILE_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\((file://(?:[^()]+|\([^)]*\))+)\)")

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

_FILES_PROMPT = """\
# Vibe Remote

Vibe Remote is a middleware layer that connects AI agents to IM platforms such as Slack. \
The user is interacting with you through an IM app via Vibe Remote forwarding.

You have optional reply-enhancement capabilities:

## 1. Send files
You can send a local file to the user by using a Markdown link with the `file://` protocol:
Example: [File 1](file:///tmp/result.pdf)
Vibe Remote will automatically send the file as an attachment.

### Image syntax
If you want it sent as an image attachment rather than a regular file, use Markdown image syntax:
Example: ![Page screenshot](file:///tmp/screenshot.jpg)
"""

_QUICK_REPLIES_PROMPT = """\

## 2. Quick-reply buttons
At the very end of the message, add a `---` separator followed by `[button text]` to provide clickable quick replies. Example:
---
[👌 Continue] | [✅ Submit PR] | [👀 Review first]
Rules:
- Infer likely next replies from the conversation context and the user's habits; only add them when they are genuinely helpful
- Do not add filler unrelated to the user's likely next intent, such as: got it, received, thanks
- They must appear at the very end of the message, after the `---` separator
- Wrap each button in `[text]` and separate them with `|`; you may start with emoji to improve clarity
- Use at most 2-4 buttons, each no longer than 20 characters
"""

_SCHEDULED_TASKS_PROMPT = """\

## 3. Scheduled tasks
You can create scheduled tasks with the `vibe task add` command.

Current conversation targeting:
- Default session key: `{session_key}`
- Current thread ID: `{thread_id}`

Rules:
- The default session key intentionally does not include a thread ID.
- Only append `::thread::<thread_id>` to `--session-key` when the user explicitly wants the scheduled task to keep replying in the current thread.
- Use `--cron "<expr>"` for recurring tasks or `--at "<ISO-8601>"` for one-off tasks.
- Use `--prompt "..."` or `--prompt-file <path>` to provide the task content.

Examples:
- Default scope: `vibe task add --session-key '{session_key}' --cron '0 * * * *' --prompt 'Send the hourly update.'`
- Current thread only when needed: `vibe task add --session-key '{session_key_with_thread}' --cron '0 9 * * *' --prompt 'Post the daily summary in this thread.'`
"""

_VIBE_SKILL_PROMPT = """\

## 4. Vibe Remote skill
When the user asks you to configure, repair, or operate Vibe Remote itself, read `https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md` before making changes.
Use that skill for configuration file locations, scope rules, routing behavior, scheduled-task operations, and troubleshooting steps.
"""


def _build_scheduled_tasks_prompt(context: MessageContext, *, fallback_platform: Optional[str] = None) -> str:
    from core.scheduled_tasks import build_session_key_for_context

    default_key = build_session_key_for_context(
        context,
        include_thread=False,
        fallback_platform=fallback_platform,
    ).to_key(include_thread=False)
    thread_id = context.thread_id or "(none)"
    if context.thread_id:
        session_key_with_thread = f"{default_key}::thread::{context.thread_id}"
    else:
        session_key_with_thread = f"{default_key}::thread::<thread_id>"
    return _SCHEDULED_TASKS_PROMPT.format(
        session_key=default_key,
        thread_id=thread_id,
        session_key_with_thread=session_key_with_thread,
    )


def build_reply_enhancements_prompt(
    *,
    include_quick_replies: bool = True,
    context: Optional[MessageContext] = None,
    fallback_platform: Optional[str] = None,
) -> str:
    """Build the reply-enhancement prompt for the current platform/backend."""

    prompt = _FILES_PROMPT
    if include_quick_replies:
        prompt += _QUICK_REPLIES_PROMPT
    if context is not None:
        prompt += _build_scheduled_tasks_prompt(context, fallback_platform=fallback_platform)
    prompt += _VIBE_SKILL_PROMPT
    return prompt


REPLY_ENHANCEMENTS_PROMPT = build_reply_enhancements_prompt()
