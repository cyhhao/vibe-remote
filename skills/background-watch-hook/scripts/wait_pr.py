#!/usr/bin/env python3
"""Wait until a GitHub pull request or repository receives new PR activity."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _github_wait_common import (  # noqa: E402
    filter_new,
    get_authenticated_login,
    get_token,
    list_paginated,
    list_paginated_with_count,
    max_id,
    min_interval_for_unauthenticated,
    requests_per_poll,
    squash,
)

CODEX_REVIEW_PASS_REACTION_USER = "chatgpt-codex-connector[bot]"
CODEX_REVIEW_PASS_REACTION_CONTENT = "+1"


def _format_review(review: dict[str, Any]) -> str:
    review_id = review.get("id")
    author = ((review.get("user") or {}).get("login")) or "unknown"
    state = str(review.get("state") or "commented").lower()
    body = squash(review.get("body") or state)
    url = review.get("html_url") or ""
    return f"- review #{review_id} by {author} ({state})\n  {body}\n  {url}"


def _format_review_comment(comment: dict[str, Any]) -> str:
    comment_id = comment.get("id")
    author = ((comment.get("user") or {}).get("login")) or "unknown"
    path = comment.get("path") or "unknown-path"
    body = squash(comment.get("body"))
    url = comment.get("html_url") or ""
    return f"- review_comment #{comment_id} by {author} on {path}\n  {body}\n  {url}"


def _format_issue_comment(comment: dict[str, Any]) -> str:
    comment_id = comment.get("id")
    author = ((comment.get("user") or {}).get("login")) or "unknown"
    body = squash(comment.get("body"))
    url = comment.get("html_url") or ""
    return f"- issue_comment #{comment_id} by {author}\n  {body}\n  {url}"


def _is_self_authored_comment(comment: dict[str, Any], viewer_login: str | None) -> bool:
    if not viewer_login:
        return False
    author = ((comment.get("user") or {}).get("login")) or ""
    return str(author).casefold() == viewer_login.casefold()


def _is_codex_pass_reaction(reaction: dict[str, Any]) -> bool:
    author = ((reaction.get("user") or {}).get("login")) or ""
    content = str(reaction.get("content") or "")
    return author == CODEX_REVIEW_PASS_REACTION_USER and content == CODEX_REVIEW_PASS_REACTION_CONTENT


def _format_reaction(reaction: dict[str, Any]) -> str:
    reaction_id = reaction.get("id")
    author = ((reaction.get("user") or {}).get("login")) or "unknown"
    content = str(reaction.get("content") or "")
    created_at = str(reaction.get("created_at") or "")
    return (
        f"- pr_reaction #{reaction_id} by {author} ({content})\n"
        f"  Codex review completed without comments and reacted on the PR body at {created_at}."
    )


def _format_pull_request(pr: dict[str, Any]) -> str:
    pr_number = pr.get("number")
    author = ((pr.get("user") or {}).get("login")) or "unknown"
    state = str(pr.get("state") or "open").lower()
    title = squash(pr.get("title") or "")
    url = pr.get("html_url") or ""
    return f"- pull_request #{pr_number} by {author} ({state})\n  {title}\n  {url}"


def _fetch_state(repo: str, pr_number: int, token: str | None) -> tuple[dict[str, list[dict[str, Any]]], int]:
    encoded_repo = urllib.parse.quote(repo, safe="/")
    reviews, review_requests = list_paginated_with_count(
        f"https://api.github.com/repos/{encoded_repo}/pulls/{pr_number}/reviews",
        token,
    )
    review_comments, review_comment_requests = list_paginated_with_count(
        f"https://api.github.com/repos/{encoded_repo}/pulls/{pr_number}/comments",
        token,
    )
    issue_comments, issue_comment_requests = list_paginated_with_count(
        f"https://api.github.com/repos/{encoded_repo}/issues/{pr_number}/comments",
        token,
    )
    reactions, reaction_requests = list_paginated_with_count(
        f"https://api.github.com/repos/{encoded_repo}/issues/{pr_number}/reactions",
        token,
    )
    return (
        {
            "reviews": reviews,
            "review_comments": review_comments,
            "issue_comments": issue_comments,
            "reactions": reactions,
        },
        review_requests + review_comment_requests + issue_comment_requests + reaction_requests,
    )


def _fetch_new_pr_state(
    repo: str,
    token: str | None,
    *,
    stop_after_id: int | None = None,
    max_pages: int | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    encoded_repo = urllib.parse.quote(repo, safe="/")
    pull_requests, request_count = list_paginated_with_count(
        f"https://api.github.com/repos/{encoded_repo}/pulls?state=all&sort=created&direction=desc",
        token,
        stop_after_id=stop_after_id,
        max_pages=max_pages,
    )
    return {"pull_requests": pull_requests}, request_count


def _render_activity(
    *,
    repo: str,
    pr_number: int,
    state: dict[str, list[dict[str, Any]]],
    review_cursor: int,
    review_comment_cursor: int,
    issue_comment_cursor: int,
    reaction_cursor: int,
    event_limit: int,
    viewer_login: str | None = None,
    ignore_self_comments: bool = True,
) -> tuple[str | None, int, int, int, int]:
    new_reviews = filter_new(state["reviews"], review_cursor)
    new_review_comments = filter_new(state["review_comments"], review_comment_cursor)
    new_issue_comments = filter_new(state["issue_comments"], issue_comment_cursor)
    visible_review_comments = (
        [comment for comment in new_review_comments if not _is_self_authored_comment(comment, viewer_login)]
        if ignore_self_comments
        else new_review_comments
    )
    visible_issue_comments = (
        [comment for comment in new_issue_comments if not _is_self_authored_comment(comment, viewer_login)]
        if ignore_self_comments
        else new_issue_comments
    )
    new_reactions = [
        reaction
        for reaction in filter_new(state["reactions"], reaction_cursor)
        if _is_codex_pass_reaction(reaction)
    ]

    if not (new_reviews or new_review_comments or new_issue_comments or new_reactions):
        return None, review_cursor, review_comment_cursor, issue_comment_cursor, reaction_cursor

    next_review_cursor = max(review_cursor, max_id(new_reviews))
    next_review_comment_cursor = max(review_comment_cursor, max_id(new_review_comments))
    next_issue_comment_cursor = max(issue_comment_cursor, max_id(new_issue_comments))
    next_reaction_cursor = max(reaction_cursor, max_id(state["reactions"]))

    rendered_events: list[str] = []
    rendered_events.extend(_format_review(review) for review in new_reviews)
    rendered_events.extend(_format_review_comment(comment) for comment in visible_review_comments)
    rendered_events.extend(_format_issue_comment(comment) for comment in visible_issue_comments)
    rendered_events.extend(_format_reaction(reaction) for reaction in new_reactions)

    if not rendered_events:
        return (
            None,
            next_review_cursor,
            next_review_comment_cursor,
            next_issue_comment_cursor,
            next_reaction_cursor,
        )

    lines = [f"GitHub PR activity detected for {repo}#{pr_number}"]

    visible_limit = max(event_limit, 1)
    for entry in rendered_events[:visible_limit]:
        lines.append(entry)

    total_events = len(rendered_events)
    if total_events > visible_limit:
        lines.append(f"- {total_events - visible_limit} additional event(s) omitted")

    return (
        "\n".join(lines),
        next_review_cursor,
        next_review_comment_cursor,
        next_issue_comment_cursor,
        next_reaction_cursor,
    )


def _render_new_pull_requests(
    *,
    repo: str,
    state: dict[str, list[dict[str, Any]]],
    pr_cursor: int,
    event_limit: int,
) -> tuple[str | None, int]:
    new_pull_requests = filter_new(state["pull_requests"], pr_cursor)
    if not new_pull_requests:
        return None, pr_cursor

    next_pr_cursor = max(pr_cursor, max_id(new_pull_requests))
    lines = [f"GitHub new pull request activity detected for {repo}"]
    rendered_events = [_format_pull_request(pr) for pr in new_pull_requests]

    visible_limit = max(event_limit, 1)
    for entry in rendered_events[:visible_limit]:
        lines.append(entry)

    total_events = len(rendered_events)
    if total_events > visible_limit:
        lines.append(f"- {total_events - visible_limit} additional event(s) omitted")

    return "\n".join(lines), next_pr_cursor


def _write_cursor_output(
    path: str | None,
    *,
    review_cursor: int,
    review_comment_cursor: int,
    issue_comment_cursor: int,
    reaction_cursor: int,
) -> None:
    if not path:
        return

    payload = {
        "review_cursor": review_cursor,
        "review_comment_cursor": review_comment_cursor,
        "issue_comment_cursor": issue_comment_cursor,
        "reaction_cursor": reaction_cursor,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _write_new_pr_cursor_output(path: str | None, *, pr_cursor: int) -> None:
    if not path:
        return

    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"pr_cursor": pr_cursor}, handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub repo in owner/name form")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pr", type=int, help="Pull request number")
    mode.add_argument("--new-prs", action="store_true", help="Watch for new pull requests in the repository")
    parser.add_argument("--interval", type=float, default=45.0, help="Polling interval in seconds")
    parser.add_argument(
        "--timeout",
        type=float,
        default=21600.0,
        help="Overall timeout in seconds; default 21600 (6 hours), 0 means forever",
    )
    parser.add_argument("--since-review-id", type=int, default=None, help="Existing review cursor")
    parser.add_argument("--since-review-comment-id", type=int, default=None, help="Existing review comment cursor")
    parser.add_argument("--since-issue-comment-id", type=int, default=None, help="Existing PR conversation comment cursor")
    parser.add_argument("--since-reaction-id", type=int, default=None, help="Existing PR-body reaction cursor")
    parser.add_argument("--since-pr-id", type=int, default=None, help="Existing repository pull request cursor")
    parser.add_argument("--cursor-output", help=argparse.SUPPRESS)
    parser.add_argument("--event-limit", type=int, default=8, help="Maximum number of new events to include in stdout")
    parser.add_argument(
        "--include-self-comments",
        action="store_true",
        help="Include comments authored by the current authenticated GitHub user",
    )
    parser.add_argument(
        "--catch-up",
        action="store_true",
        help="Treat current existing activity as pending when no explicit cursor is provided",
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow polling without GitHub auth; the interval will be clamped to a safer minimum",
    )
    args = parser.parse_args()

    token = get_token()
    viewer_login = None if args.include_self_comments else get_authenticated_login(token)
    if token is None and not args.allow_unauthenticated:
        print(
            (
                "GitHub authentication is required for reliable polling. "
                "Set GITHUB_TOKEN/GH_TOKEN, run 'gh auth login', or pass "
                "--allow-unauthenticated for a throttled best-effort run."
            ),
            file=sys.stderr,
        )
        return 2

    base_interval = max(args.interval, 1.0)
    effective_interval = base_interval

    start = time.monotonic()

    try:
        if args.pr is not None:
            state, requests_per_poll_count = _fetch_state(args.repo, args.pr, token)
        else:
            initial_pr_stop_after_id = None
            initial_pr_max_pages = None
            if args.since_pr_id is not None and not args.catch_up:
                initial_pr_stop_after_id = args.since_pr_id
            elif not args.catch_up:
                initial_pr_max_pages = 1
            state, requests_per_poll_count = _fetch_new_pr_state(
                args.repo,
                token,
                stop_after_id=initial_pr_stop_after_id,
                max_pages=initial_pr_max_pages,
            )
    except urllib.error.HTTPError as err:
        print(f"GitHub API error: {err.code} {err.reason}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001
        print(f"Failed to fetch initial PR state: {err}", file=sys.stderr)
        return 1

    if token is None:
        bootstrap_requests = requests_per_poll_count
        unauthenticated_min = min_interval_for_unauthenticated(
            requests_per_poll_count,
            bootstrap_requests=bootstrap_requests,
        )
        startup_interval = max(base_interval, unauthenticated_min)
        if effective_interval < startup_interval:
            print(
                (
                    "No GitHub token detected; clamping polling interval from %.1fs to %.1fs "
                    "for %s request(s) per poll plus %s bootstrap request(s) to avoid "
                    "unauthenticated rate-limit lockout."
                )
                % (effective_interval, startup_interval, requests_per_poll_count, bootstrap_requests),
                file=sys.stderr,
            )
            effective_interval = startup_interval
    else:
        bootstrap_requests = 0

    if args.pr is not None:
        review_cursor = (
            args.since_review_id
            if args.since_review_id is not None
            else (0 if args.catch_up else max_id(state["reviews"]))
        )
        review_comment_cursor = (
            args.since_review_comment_id
            if args.since_review_comment_id is not None
            else (0 if args.catch_up else max_id(state["review_comments"]))
        )
        issue_comment_cursor = (
            args.since_issue_comment_id
            if args.since_issue_comment_id is not None
            else (0 if args.catch_up else max_id(state["issue_comments"]))
        )
        reaction_cursor = (
            args.since_reaction_id
            if args.since_reaction_id is not None
            else (0 if args.catch_up else max_id(state["reactions"]))
        )

        print(
            (
                "Watching GitHub PR %s#%s from cursors: review=%s review_comment=%s issue_comment=%s reaction=%s catch_up=%s"
                % (
                    args.repo,
                    args.pr,
                    review_cursor,
                    review_comment_cursor,
                    issue_comment_cursor,
                    reaction_cursor,
                    args.catch_up,
                )
            ),
            file=sys.stderr,
        )

        initial_output, review_cursor, review_comment_cursor, issue_comment_cursor, reaction_cursor = _render_activity(
            repo=args.repo,
            pr_number=args.pr,
            state=state,
            review_cursor=review_cursor,
            review_comment_cursor=review_comment_cursor,
            issue_comment_cursor=issue_comment_cursor,
            reaction_cursor=reaction_cursor,
            event_limit=args.event_limit,
            viewer_login=viewer_login,
            ignore_self_comments=not args.include_self_comments,
        )
        if initial_output is not None:
            _write_cursor_output(
                args.cursor_output,
                review_cursor=review_cursor,
                review_comment_cursor=review_comment_cursor,
                issue_comment_cursor=issue_comment_cursor,
                reaction_cursor=reaction_cursor,
            )
            print(initial_output)
            return 0
    else:
        pr_cursor = args.since_pr_id if args.since_pr_id is not None else (0 if args.catch_up else max_id(state["pull_requests"]))
        print(
            f"Watching GitHub new PRs in {args.repo} from cursor: pr={pr_cursor} catch_up={args.catch_up}",
            file=sys.stderr,
        )
        initial_output, pr_cursor = _render_new_pull_requests(
            repo=args.repo,
            state=state,
            pr_cursor=pr_cursor,
            event_limit=args.event_limit,
        )
        if initial_output is not None:
            _write_new_pr_cursor_output(args.cursor_output, pr_cursor=pr_cursor)
            print(initial_output)
            return 0

    while True:
        sleep_seconds = effective_interval
        if args.timeout > 0:
            remaining_timeout = args.timeout - (time.monotonic() - start)
            if remaining_timeout <= 0:
                print("Timed out while waiting for GitHub PR activity", file=sys.stderr)
                return 124
            sleep_seconds = min(sleep_seconds, remaining_timeout)

        time.sleep(sleep_seconds)

        try:
            if args.pr is not None:
                state, requests_per_poll_count = _fetch_state(args.repo, args.pr, token)
            else:
                state, requests_per_poll_count = _fetch_new_pr_state(
                    args.repo,
                    token,
                    stop_after_id=pr_cursor if pr_cursor > 0 else None,
                )
        except urllib.error.HTTPError as err:
            if token is None and err.code in {403, 429}:
                print(
                    (
                        "GitHub unauthenticated polling hit a rate limit. "
                        "Authenticate with 'gh auth login' or GITHUB_TOKEN/GH_TOKEN."
                    ),
                    file=sys.stderr,
                )
                return 1
            print(f"GitHub API error during polling: {err.code} {err.reason}", file=sys.stderr)
            continue
        except Exception as err:  # noqa: BLE001
            print(f"Polling failed: {err}", file=sys.stderr)
            continue

        if token is None:
            unauthenticated_min = min_interval_for_unauthenticated(requests_per_poll_count)
            target_interval = max(base_interval, unauthenticated_min)
            if target_interval != effective_interval:
                if target_interval > effective_interval:
                    print(
                        (
                            "GitHub unauthenticated polling now needs %.1fs minimum for %s request(s) "
                            "per poll; increasing interval."
                        )
                        % (target_interval, requests_per_poll_count),
                        file=sys.stderr,
                    )
                else:
                    print(
                        (
                            "GitHub unauthenticated polling now needs only %.1fs minimum for %s request(s) "
                            "per poll; reducing interval."
                        )
                        % (target_interval, requests_per_poll_count),
                        file=sys.stderr,
                    )
                effective_interval = target_interval

        if args.pr is not None:
            output, review_cursor, review_comment_cursor, issue_comment_cursor, reaction_cursor = _render_activity(
                repo=args.repo,
                pr_number=args.pr,
                state=state,
                review_cursor=review_cursor,
                review_comment_cursor=review_comment_cursor,
                issue_comment_cursor=issue_comment_cursor,
                reaction_cursor=reaction_cursor,
                event_limit=args.event_limit,
                viewer_login=viewer_login,
                ignore_self_comments=not args.include_self_comments,
            )
            if output is None:
                continue

            _write_cursor_output(
                args.cursor_output,
                review_cursor=review_cursor,
                review_comment_cursor=review_comment_cursor,
                issue_comment_cursor=issue_comment_cursor,
                reaction_cursor=reaction_cursor,
            )
        else:
            output, pr_cursor = _render_new_pull_requests(
                repo=args.repo,
                state=state,
                pr_cursor=pr_cursor,
                event_limit=args.event_limit,
            )
            if output is None:
                continue
            _write_new_pr_cursor_output(args.cursor_output, pr_cursor=pr_cursor)

        print(output)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
