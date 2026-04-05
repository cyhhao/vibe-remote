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

from config import paths
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


def strip_file_links(text: str) -> str:
    """Remove ``file://`` markdown URLs while preserving the surrounding text."""
    files = _extract_file_links(text)
    if not files:
        return text
    return _strip_file_links(text)


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

Vibe Remote is a middleware layer that connects AI agents to IM platforms such as Slack, Discord, Telegram, WeChat, and Lark/Feishu. \
The user is interacting with you through an IM app via Vibe Remote forwarding.

If the user asks you to configure, repair, or operate Vibe Remote itself, read `https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md` before making changes. Use it for configuration file locations, scope rules, routing behavior, scheduled-task operations, and troubleshooting steps.

Vibe Remote provides optional capabilities:

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

## 3. Scheduled tasks, watches, and hooks
Use `vibe task add` for saved work that should run later on a schedule or at one exact time.
Use `vibe watch add` for managed background waiters that should keep running until a condition is met and then send a follow-up.
Use `vibe hook send --session-key ... --prompt ...` for one-shot asynchronous sends without saving a task or watch.

Current conversation targeting:
- Default session key: `{default_session_key}`
- Channel-level session key: `{channel_session_key}`

Rules:
- `session_key` controls the conversation scope that Vibe Remote will continue using.
- When you do not want to keep the current session and instead want to start or reuse a higher-level session, usually use the higher-level session key. For example, if the default key is `slack::channel::C123::thread::171717.123`, then `slack::channel::C123` creates or reuses the channel-scoped session.
- `--post-to` changes the delivery target, not the session scope. Use `--post-to channel` when the session should stay thread-scoped but the follow-up message should be posted to the parent channel.
- Use `--cron "<expr>"` for recurring tasks or `--at "<ISO-8601>"` for one-off stored tasks.
- Use `vibe watch list`, `vibe watch show`, `vibe watch pause`, `vibe watch resume`, and `vibe watch remove` to manage background work after creation.
- Prefer `vibe watch add` over ad-hoc `nohup` or shell-detached jobs when the user wants a managed background task.
- If `--timezone` is omitted, the task uses the local system timezone at creation time.
- Use `--prompt "..."` or `--prompt-file <path>` for task and hook content. Use `--prefix "..."` on watches for the follow-up instruction that is prepended before waiter stdout; when both exist, Vibe Remote joins them with a blank line.
- If this is your first time using these commands, read `vibe task add --help`, `vibe watch add --help`, or `vibe hook send --help` before creating anything. The help text and relevant skills explain not just the argument syntax but also runtime effects such as how follow-up messages are built and how tasks or watches are stored and managed.
"""


_USER_PREFERENCES_PROMPT = """\

## 4. User Context and Preferences
A shared user context and preferences file is available at `{preferences_path}`.

From first principles, serving the user better means thinking proactively about how to make full use of the available context, reduce repetitive communication, and make judgments that better fit the user's habits. For example, the user may currently be receiving your messages through an IM channel, possibly on a mobile device or in a fragmented-attention context.

Use this file proactively when it is helpful, especially when it can help you understand the user's stable habits, preferences, or working style, reduce repeated questions, and choose among multiple reasonable ways to proceed in a way that better fits the user.

You do not need to read it for every simple request; but if consulting it could improve personalization, efficiency, or continuity, prefer checking it early.

You may also update it, usually in the current user's section: `{user_section}`.
Only record durable, factual, reusable information there.
Keep entries short, deduplicated, and free of secrets unless the user explicitly asks.
"""


def _build_prompt_session_key(
    context: MessageContext,
    *,
    include_thread: bool,
    fallback_platform: Optional[str] = None,
) -> str:
    platform_specific = context.platform_specific or {}
    platform = context.platform or platform_specific.get("platform") or fallback_platform or "<platform>"
    is_dm = bool(platform_specific.get("is_dm", False))
    scope_type = "user" if is_dm else "channel"
    scope_id = context.user_id if is_dm else context.channel_id
    base = f"{platform}::{scope_type}::{scope_id}"
    if include_thread and context.thread_id:
        return f"{base}::thread::{context.thread_id}"
    return base


def _build_scheduled_tasks_prompt(context: MessageContext, *, fallback_platform: Optional[str] = None) -> str:
    default_key = _build_prompt_session_key(
        context,
        include_thread=True,
        fallback_platform=fallback_platform,
    )
    channel_key = _build_prompt_session_key(
        context,
        include_thread=False,
        fallback_platform=fallback_platform,
    )
    return _SCHEDULED_TASKS_PROMPT.format(
        default_session_key=default_key,
        channel_session_key=channel_key,
    )


def _build_user_preferences_prompt(
    context: Optional[MessageContext],
    *,
    fallback_platform: Optional[str] = None,
) -> str:
    platform = fallback_platform
    user_id = "<user_id>"
    if context is not None:
        platform_specific = context.platform_specific or {}
        platform = context.platform or platform_specific.get("platform") or fallback_platform
        user_id = context.user_id or "<user_id>"
    user_section = f"{platform or '<platform>'}/{user_id}"
    return _USER_PREFERENCES_PROMPT.format(
        preferences_path=f"`{paths.get_user_preferences_path()}`",
        user_section=user_section,
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
    prompt += _build_user_preferences_prompt(context, fallback_platform=fallback_platform)
    return prompt


REPLY_ENHANCEMENTS_PROMPT = build_reply_enhancements_prompt()
