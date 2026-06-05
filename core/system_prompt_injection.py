"""System prompt injection helpers for Vibe Remote agent backends."""

from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Iterable, Optional

from config import paths
from core.avibe_cloud import AVIBE_CLOUD_CONNECT_GUIDANCE
from modules.im import MessageContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentPromptInfo:
    name: str
    description: str
    backend: str = "unknown"
    cli_token: str = ""


_BASE_CAPABILITIES_INTRO = """\
# Vibe Remote

"""

_BASE_CAPABILITIES_BODY = """\
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

_SESSION_START_PROMPT = """\
Current session id: `{default_session_id}`. Treat this as the authoritative Vibe Remote agent session for this conversation.

"""

_SHOW_PAGES_PROMPT = """\

## Show Pages
When a visual page would help the user understand a problem, plan, process, result, or complex information more clearly, use Show Pages. They are useful for diagrams, flowcharts, mind maps, timelines, architecture maps, comparison views, dashboards, visual reports, interactive explanations, and small prototypes.

Each Agent Session has one Show Page. Get this session's page directory:

`vibe show path --session-id $default_session_id`

Check status:

`vibe show status --session-id $default_session_id`

Change visibility:

`vibe show update --session-id $default_session_id --visibility public`
`vibe show update --session-id $default_session_id --visibility private`
`vibe show update --session-id $default_session_id --visibility offline`

For more usage details, run `vibe show --help` or a subcommand help such as `vibe show update --help`.
$avibe_cloud_guidance_section
Guidance:
- New Show Page workspaces are managed React/Vite apps. Edit `src/App.tsx`, `src/styles.css`, and optional `api/*.ts` handler files. Do not replace `index.html` or `src/main.tsx` unless you are repairing the app shell.
- The standard structure is `index.html`, `src/main.tsx`, `src/App.tsx`, `src/styles.css`, and optional `api/*.ts`; treat `index.html` and `src/main.tsx` as the runtime-owned app shell.
- Hot reload is available while `/show/<session-id>/` is open. Users will see page changes live. Prefer component-level changes that preserve React state.
- Built-in UI imports include shadcn-style aliases such as `@/components/ui/button`, `@/components/ui/card`, `@/components/ui/badge`, `@/components/ui/dialog`, `@/components/ui/input`, `@/components/ui/progress`, plus `@avibe/show-ui/theme` for theme presets and CSS variables.
- Prefer the built-in UI primitives over hand-rolled controls. They include Show Page motion for changed text, numbers, badges, cards, and progress without extra animation calls.
- Optional server handlers live under `api/` and run only when requested. Export functions named like HTTP methods, for example `export async function GET(request) { return Response.json({ ok: true }) }`.
- Design for user understanding, not just for moving text onto a webpage. Choose the visual form that best helps the user inspect, compare, confirm, and continue the discussion.
- Use diagrams or mind maps for relationships, flowcharts or state machines for processes, timelines for sequences, charts or dashboards for metrics, and side-by-side views for tradeoffs.
- Make the page visually polished: use clear hierarchy, spacing, typography, contrast, and consistent components. Avoid rough default-looking pages.
- Make the page work reasonably on mobile because users may open links from an IM app on their phone.
- Prefer React component implementations. Useful visualization libraries include React Flow, Mermaid, Markmap, Chart.js, and Cytoscape.js.
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
        "using a real file URI under the local Codex generated_images directory, for example: "
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

_HARNESS_PROMPT = """\

## Harness
Use Vibe Remote Harness when the user's goal needs work to run later, repeat on a schedule, wait on an external condition, continue in the background, or be delegated to a purpose-built Agent.

For complex requests, reason from first principles and tacit knowledge before choosing a response. Ask what outcome the user is really trying to secure, what should keep happening after this turn, what signals would prove progress, and whether the real need is a repeatable operating loop rather than a one-off answer. When that is true, build or improve an Agent Harness: create or tune Agents, connect them with tasks, watches, and Agent runs, and turn the work into a reliable workflow instead of quickly completing only the visible step.

### Task / Watch
Use `vibe task add` to create a scheduled task that sends a preset message to an Agent at one exact time or on a recurring schedule.
Use `vibe watch add` to create managed monitoring tasks, usually backed by a small custom script. It is useful for waiting on external conditions such as a PR review becoming actionable, a CI/deploy finishing, a log pattern appearing, a file being generated, or a service health check turning green; when the condition is met, the watch sends a follow-up back into the session.

Current conversation targeting:
- Current session id: `{default_session_id}`
- Current Agent backend: `{current_agent_backend}`

Rules:
- Use `--session-id {default_session_id}` when creating tasks, watches, or Agent runs that should continue this exact Vibe Remote agent session.
- `--post-to` changes the delivery target, not the session scope. Use `--post-to channel` when the session should stay thread-scoped but the follow-up message should be posted to the parent channel.
- Use `--cron "<expr>"` for recurring tasks or `--at "<ISO-8601>"` for one-off stored tasks.
- Use `vibe task list`, `vibe task show <id>`, `vibe task pause <id>`, `vibe task resume <id>`, `vibe task run <id>`, and `vibe task remove <id>` to inspect and manage scheduled tasks.
- Use `vibe watch list`, `vibe watch show <id>`, `vibe watch pause <id>`, `vibe watch resume <id>`, and `vibe watch remove <id>` to inspect and manage watches.
- Prefer `vibe watch add` over ad-hoc `nohup` or shell-detached jobs when the user wants a managed background task.
- If `--timezone` is omitted, the task uses the local system timezone at creation time.
- For tasks, use `--message "..."` or `--message-file <path>` as the stored message. For watches, use `--prefix "..."` for the follow-up instruction prepended before waiter stdout; when both message and waiter output exist, Vibe Remote joins them with a blank line.
- If this is your first time using task or watch commands, read `vibe task add --help` or `vibe watch add --help` before creating anything. The help text explains not just argument syntax but also runtime effects such as how follow-up messages are built and how tasks or watches are stored and managed.

### Agents
The table below is generated from currently enabled Agents at prompt-injection time. It must reflect live Agent definitions; do not hard-code Agent names, CLI tokens, backends, or descriptions.

{enabled_agents_table}

Rules:
- All Agents listed in the generated table are enabled. Use the `CLI Token` value, not the display name, in shell commands such as `vibe agent show <cli-token>` and `vibe agent run --agent <cli-token> ...`.
- When reusing the current `--session-id`, use only Agents whose `Backend` matches the current Agent backend `{current_agent_backend}`. If the desired Agent uses another backend, start a fresh one-shot session with `--create-session` instead of reusing the current session id.
- If this is your first time running or inspecting Agents, read `vibe agent run --help`, `vibe runs list --help`, or `vibe runs show --help` before acting.
- For a synchronous Agent turn, use `vibe agent run --agent <cli-token> --session-id ... --message ...`; the CLI waits for the run result, bounded by `--wait-timeout` when provided.
- For background delegation, add `--async`: `vibe agent run --async --agent <cli-token> --session-id ... --message ...`. Use this for one-shot background work that should not be saved as a recurring task or a watch.
- To follow up in the same Agent Session, keep the same `--session-id` and send the next message with `vibe agent run --agent <cli-token> --session-id ... --message ...`. Use `--create-session` when a fresh one-shot Agent Session is intended.
- Inspect Agent run records with `vibe runs list`, commonly filtered by `--session-id`, `--agent`, `--status`, or `--created-after`; then use `vibe runs show <run_id>` for the full run record or `vibe runs cancel <run_id>` for best-effort cancellation.
- When the user's goal suggests a repeatable workflow, consider whether to create a new Agent with `vibe agent create`, or update an existing Agent's description, model, reasoning effort, metadata, or system prompt with `vibe agent update`.
- Combine Agents with Harness commands: use tasks for scheduled or recurring work, watches for external wait conditions, and async Agent runs for one-shot background delegation.
- Do not create or modify Agents casually. Use this path when it reduces repeated prompting, captures a reusable role, or gives the user a more reliable long-running Harness.
"""

_SESSION_END_PROMPT = """\

## Current Session Reminder
Current session id: `{default_session_id}`. Before using Show Page or Harness commands, target this exact session unless the user explicitly asks to target a different one.
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


def _extract_default_session_id(context: MessageContext) -> str:
    platform_specific = context.platform_specific or {}
    default_session_id = platform_specific.get("agent_session_id")
    if not default_session_id:
        raise ValueError("agent_session_id is required before building Vibe Remote capability prompt")
    return str(default_session_id)


def _coerce_agent_prompt_info(agent: Any) -> AgentPromptInfo:
    if isinstance(agent, dict):
        name = str(agent.get("name") or "").strip()
        description = str(agent.get("description") or "").strip()
        backend = str(agent.get("backend") or "").strip()
        cli_token = str(agent.get("cli_token") or agent.get("normalized_name") or "").strip()
    else:
        name = str(getattr(agent, "name", "") or "").strip()
        description = str(getattr(agent, "description", "") or "").strip()
        backend = str(getattr(agent, "backend", "") or "").strip()
        cli_token = str(
            getattr(agent, "cli_token", "") or getattr(agent, "normalized_name", "") or ""
        ).strip()
    if not name:
        raise ValueError("agent name is required")
    return AgentPromptInfo(
        name=name,
        description=description or "(no description)",
        backend=backend or "unknown",
        cli_token=cli_token or _fallback_agent_cli_token(name),
    )


def _fallback_agent_cli_token(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")
    if normalized:
        return normalized
    return shlex.quote(str(name).strip())


def _escape_markdown_table_cell(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _format_enabled_agents_table(enabled_agents: Optional[Iterable[Any]]) -> str:
    if enabled_agents is None:
        return (
            "No enabled Agents were provided in this prompt context. "
            "Before invoking an Agent, run `vibe agent list` and only use names shown as enabled."
        )

    rows: list[AgentPromptInfo] = []
    for agent in enabled_agents:
        try:
            rows.append(_coerce_agent_prompt_info(agent))
        except ValueError:
            logger.debug("Skipping enabled Agent prompt row with no name: %r", agent)

    if not rows:
        return (
            "No Agents are currently enabled. "
            "Do not run `vibe agent show` or `vibe agent run` until `vibe agent list` shows an enabled Agent."
        )

    lines = ["| Agent Name | CLI Token | Backend | Agent Description |", "| --- | --- | --- | --- |"]
    for agent in sorted(rows, key=lambda item: item.name.lower()):
        lines.append(
            f"| {_escape_markdown_table_cell(agent.name)} | "
            f"{_escape_markdown_table_cell(agent.cli_token)} | "
            f"{_escape_markdown_table_cell(agent.backend)} | "
            f"{_escape_markdown_table_cell(agent.description)} |"
        )
    return "\n".join(lines)


def get_enabled_agents_for_prompt(controller: Any) -> Optional[list[AgentPromptInfo]]:
    store = getattr(controller, "vibe_agent_store", None)
    if store is None:
        return None
    try:
        agents = store.list_agents(include_disabled=False)
    except Exception as exc:
        logger.warning("Failed to list enabled Agents for prompt injection: %s", exc)
        return None
    rows: list[AgentPromptInfo] = []
    for agent in agents:
        try:
            rows.append(_coerce_agent_prompt_info(agent))
        except ValueError:
            logger.debug("Skipping enabled Agent prompt row with no name: %r", agent)
    return rows


def _build_session_start_prompt(context: MessageContext) -> str:
    return _SESSION_START_PROMPT.format(default_session_id=_extract_default_session_id(context))


def _build_harness_prompt(
    context: MessageContext,
    *,
    enabled_agents: Optional[Iterable[Any]] = None,
    current_agent_backend: Optional[str] = None,
) -> str:
    default_session_id = _extract_default_session_id(context)
    return _HARNESS_PROMPT.format(
        default_session_id=default_session_id,
        current_agent_backend=str(current_agent_backend or "unknown").strip() or "unknown",
        enabled_agents_table=_format_enabled_agents_table(enabled_agents),
    )


def _build_show_pages_prompt(context: MessageContext, *, avibe_cloud_guidance: str | None = None) -> str:
    default_session_id = _extract_default_session_id(context)
    return Template(_SHOW_PAGES_PROMPT).substitute(
        default_session_id=default_session_id,
        avibe_cloud_guidance_section=f"\n{avibe_cloud_guidance}\n" if avibe_cloud_guidance else "\n",
    )


def _build_session_end_prompt(context: MessageContext) -> str:
    return _SESSION_END_PROMPT.format(default_session_id=_extract_default_session_id(context))


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
    enabled_agents: Optional[Iterable[Any]] = None,
    current_agent_backend: Optional[str] = None,
) -> str:
    """Build Vibe Remote system prompt additions for an agent backend."""

    prompt = _BASE_CAPABILITIES_INTRO
    if context is not None:
        prompt += _build_session_start_prompt(context)
    prompt += _BASE_CAPABILITIES_BODY
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
        prompt += _build_harness_prompt(
            context,
            enabled_agents=enabled_agents,
            current_agent_backend=current_agent_backend,
        )
    if include_user_preferences:
        prompt += _build_user_preferences_prompt(context, fallback_platform=fallback_platform)
    if context is not None:
        prompt += _build_session_end_prompt(context)
    return prompt


SYSTEM_PROMPT_INJECTION = build_system_prompt_injection()
