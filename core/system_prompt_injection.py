"""System prompt injection helpers for Vibe Remote agent backends."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from config import paths
from core.show_pages import AVIBE_CLOUD_CONNECT_GUIDANCE
from modules.im import MessageContext


_BASE_CAPABILITIES_PROMPT = """\
# Vibe Remote

Vibe Remote is a middleware layer that connects AI agents to IM platforms such as Slack, Discord, Telegram, WeChat, and Lark/Feishu. \
The user is interacting with you through an IM app via Vibe Remote forwarding.

If the user asks you to configure, repair, or operate Vibe Remote itself, read `https://github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md` before making changes. Use it for configuration file locations, scope rules, routing behavior, scheduled-task operations, and troubleshooting steps.

Vibe Remote provides optional capabilities:

## Silent replies
If you decide no user-facing response is needed, respond only with a silent block:
`<silent>reason not shown to the user</silent>`

Rules:
- Vibe Remote strips all `<silent>...</silent>` blocks before sending messages.
- If nothing remains after stripping silent blocks, Vibe Remote sends no message.
- Use this for thread messages where you have received context but should not interrupt.

## Send files
You can send a local file to the user by using a Markdown link with the `file://` protocol:
Example: [File 1](file:///tmp/result.pdf)
Vibe Remote will automatically send the file as an attachment.

### Image syntax
If you want it sent as an image attachment rather than a regular file, use Markdown image syntax:
Example: ![Page screenshot](file:///tmp/screenshot.jpg)
"""

_SHOW_PAGES_PROMPT = """\

## Show Pages
When a visual page would help the user understand a problem, plan, process, result, or complex information more clearly, use Show Pages. They are useful for diagrams, flowcharts, mind maps, timelines, architecture maps, comparison views, dashboards, visual reports, interactive explanations, and small static prototypes.

Each Agent Session has one Show Page. Get this session's page directory:

`vibe show path --session-id {default_session_id}`

Check status:

`vibe show status --session-id {default_session_id}`

List existing pages when the user asks what Show Pages exist:

`vibe show list`

Change visibility:

`vibe show update --session-id {default_session_id} --visibility public`
`vibe show update --session-id {default_session_id} --visibility private`
`vibe show update --session-id {default_session_id} --visibility offline`

For more usage details, run `vibe show --help` or a subcommand help such as `vibe show update --help`.
{avibe_cloud_guidance_section}
Guidance:
- Write `index.html` and related static assets in the Show Page directory.
- Design for user understanding, not just for moving text onto a webpage. Choose the visual form that best helps the user inspect, compare, confirm, and continue the discussion.
- Use diagrams or mind maps for relationships, flowcharts or state machines for processes, timelines for sequences, charts or dashboards for metrics, and side-by-side views for tradeoffs.
- Make the page visually polished: use clear hierarchy, spacing, typography, contrast, and consistent components. Avoid rough default-looking pages.
- Make the page work reasonably on mobile because users may open links from an IM app on their phone.
- You may choose any suitable implementation. Reference options include native HTML/CSS/JavaScript, Excalidraw-style static SVG/PNG diagrams, React Flow, Mermaid, Markmap, Chart.js, and Cytoscape.js.
- Keep pages private by default. Publish publicly only when the user asks for a shareable or public link.
- Do not publish secrets, credentials, private logs, or sensitive user data publicly.
- If a Show Page would clearly help but the user's preference is unclear, briefly ask whether they want one.
- After creating or updating a page, send the active URL and a short summary of what the page shows.
"""


def _build_codex_generated_images_prompt() -> str:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    example_uri = (codex_home / "generated_images" / "thread-id" / "image-file.png").as_uri()
    return (
        "\n### Codex-generated images\n"
        "If you generate an image with Codex, include it in the final reply with Markdown image syntax, "
        f"using a real file URI under the local Codex generated_images directory, for example: "
        f"`![generated image]({example_uri})`. "
        "Replace the example thread id and filename with the actual generated image path. "
        "Never emit variables, placeholder paths, or sandbox paths like `/mnt/data/...`; "
        "if you cannot determine the real path, leave the final reply empty.\n"
    )


_QUICK_REPLIES_PROMPT = """\

## Quick-reply buttons
At the very end of the message, add a `---` separator followed by `[button text]` to provide clickable quick replies. Example:
---
[👌 Continue] | [✅ Submit PR] | [👀 Review first]
Rules:
- Think through the tacit knowledge behind the user's words, infer their deeper intent, and suggest likely next replies from the conversation context and the user's habits
- Do not add filler unrelated to the user's likely next intent, such as: got it, received, thanks
- They must appear at the very end of the message, after the `---` separator
- Wrap each button in `[text]` and separate them with `|`; you may start with emoji to improve clarity
- Use at most 2-4 buttons, each no longer than 20 characters
"""

_SCHEDULED_TASKS_PROMPT = """\

## Scheduled tasks, watches, and hooks
Use `vibe task add` for saved work that should run later on a schedule or at one exact time.
Use `vibe watch add` for managed background waiters that should keep running until a condition is met and then send a follow-up.
Use `vibe agent run --async --session-id ... --message ...` for one-shot asynchronous sends without saving a task or watch.

Current conversation targeting:
- Current session id: `{default_session_id}`

Rules:
- Use `--session-id {default_session_id}` when scheduling work; it targets the exact Vibe Remote agent session to continue.
- `--post-to` changes the delivery target, not the session scope. Use `--post-to channel` when the session should stay thread-scoped but the follow-up message should be posted to the parent channel.
- Use `--cron "<expr>"` for recurring tasks or `--at "<ISO-8601>"` for one-off stored tasks.
- Use `vibe watch list`, `vibe watch show`, `vibe watch pause`, `vibe watch resume`, and `vibe watch remove` to manage background work after creation.
- Prefer `vibe watch add` over ad-hoc `nohup` or shell-detached jobs when the user wants a managed background task.
- If `--timezone` is omitted, the task uses the local system timezone at creation time.
- Use `--message "..."` or `--message-file <path>` for task and agent-run content. Use `--prefix "..."` on watches for the follow-up instruction that is prepended before waiter stdout; when both exist, Vibe Remote joins them with a blank line.
- If this is your first time using these commands, read `vibe task add --help`, `vibe watch add --help`, or `vibe agent run --help` before creating anything. The help text and relevant skills explain not just the argument syntax but also runtime effects such as how follow-up messages are built and how tasks or watches are stored and managed.
"""


_USER_PREFERENCES_PROMPT = """\

## User Context and Preferences
A shared user context and preferences file is available at `{preferences_path}`.

From first principles, serving the user better means thinking proactively about how to make full use of the available context, reduce repetitive communication, and make judgments that better fit the user's habits. For example, the user may currently be receiving your messages through an IM channel, possibly on a mobile device or in a fragmented-attention context.

Use this file proactively when it is helpful, especially when it can help you understand the user's stable habits, preferences, or working style, reduce repeated questions, and choose among multiple reasonable ways to proceed in a way that better fits the user.

You do not need to read it for every simple request; but if consulting it could improve personalization, efficiency, or continuity, prefer checking it early.

You may also update it when explicitly asked.
Use the current platform `{platform}` and the user id from the current message metadata to choose the appropriate user section: `{platform}/<user_id>`.
Only record durable, factual, reusable information there.
Keep entries short, deduplicated, and free of secrets unless the user explicitly asks.
"""


def _build_scheduled_tasks_prompt(context: MessageContext, *, fallback_platform: Optional[str] = None) -> str:
    platform_specific = context.platform_specific or {}
    default_session_id = platform_specific.get("agent_session_id")
    if not default_session_id:
        raise ValueError("agent_session_id is required before building Vibe Remote scheduled-task prompt")
    return _SCHEDULED_TASKS_PROMPT.format(
        default_session_id=str(default_session_id),
    )


def _build_show_pages_prompt(context: MessageContext, *, avibe_cloud_guidance: str | None = None) -> str:
    platform_specific = context.platform_specific or {}
    default_session_id = platform_specific.get("agent_session_id")
    if not default_session_id:
        raise ValueError("agent_session_id is required before building Vibe Remote capability prompt")
    return _SHOW_PAGES_PROMPT.format(
        default_session_id=str(default_session_id),
        avibe_cloud_guidance_section=f"\n{avibe_cloud_guidance}\n" if avibe_cloud_guidance else "\n",
    )


def _build_user_preferences_prompt(
    context: Optional[MessageContext],
    *,
    fallback_platform: Optional[str] = None,
) -> str:
    platform = fallback_platform or "<platform>"
    if context is not None:
        platform_specific = context.platform_specific or {}
        platform = context.platform or platform_specific.get("platform") or fallback_platform or "<platform>"
    return _USER_PREFERENCES_PROMPT.format(
        preferences_path=f"`{paths.get_user_preferences_path()}`",
        platform=platform,
    )


def build_system_prompt_injection(
    *,
    include_quick_replies: bool = True,
    include_show_pages: bool = True,
    include_codex_generated_images: bool = False,
    include_user_preferences: bool = True,
    avibe_cloud_connected: bool | None = None,
    context: Optional[MessageContext] = None,
    fallback_platform: Optional[str] = None,
) -> str:
    """Build Vibe Remote system prompt additions for an agent backend."""

    prompt = _BASE_CAPABILITIES_PROMPT
    if include_codex_generated_images:
        prompt += _build_codex_generated_images_prompt()
    if include_show_pages and context is not None:
        guidance = None
        if avibe_cloud_connected is False:
            guidance = AVIBE_CLOUD_CONNECT_GUIDANCE
        prompt += _build_show_pages_prompt(context, avibe_cloud_guidance=guidance)
    if include_quick_replies:
        prompt += _QUICK_REPLIES_PROMPT
    if context is not None:
        prompt += _build_scheduled_tasks_prompt(context, fallback_platform=fallback_platform)
    if include_user_preferences:
        prompt += _build_user_preferences_prompt(context, fallback_platform=fallback_platform)
    return prompt


SYSTEM_PROMPT_INJECTION = build_system_prompt_injection()
