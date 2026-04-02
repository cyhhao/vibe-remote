#!/usr/bin/env python3
"""Wait until a GitHub pull request receives new review activity."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _get_token() -> str | None:
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value

    gh_path = shutil.which("gh")
    if not gh_path:
        return None

    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    token = result.stdout.strip()
    return token or None


def _min_interval_for_unauthenticated(
    requests_per_poll: int,
    *,
    bootstrap_requests: int = 0,
) -> float:
    # Keep unauthenticated usage at or below GitHub's 60 requests/hour ceiling,
    # including the initial bootstrap fetch before the polling loop begins.
    recurring_requests = max(requests_per_poll, 1)
    hourly_budget = max(1, 60 - max(bootstrap_requests, 0))
    return float(max(60, math.ceil((3600 * recurring_requests) / hourly_budget)))


def _github_get(url: str, token: str | None) -> Any:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "background-watch-hook/0.1.0")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _list_paginated(base_url: str, token: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in base_url else "?"
        url = f"{base_url}{separator}per_page=100&page={page}"
        payload = _github_get(url, token)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a JSON list from {url}")
        if not payload:
            break
        items.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
        page += 1
    return items


def _squash(text: str | None, *, limit: int = 140) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _max_id(items: list[dict[str, Any]]) -> int:
    values = [int(item["id"]) for item in items if isinstance(item.get("id"), int)]
    return max(values, default=0)


def _filter_new(items: list[dict[str, Any]], since_id: int) -> list[dict[str, Any]]:
    return sorted(
        [item for item in items if isinstance(item.get("id"), int) and int(item["id"]) > since_id],
        key=lambda item: (str(item.get("created_at") or ""), int(item["id"])),
    )


def _format_review(review: dict[str, Any]) -> str:
    review_id = review.get("id")
    author = ((review.get("user") or {}).get("login")) or "unknown"
    state = str(review.get("state") or "commented").lower()
    body = _squash(review.get("body") or state)
    url = review.get("html_url") or ""
    return f"- review #{review_id} by {author} ({state})\n  {body}\n  {url}"


def _format_review_comment(comment: dict[str, Any]) -> str:
    comment_id = comment.get("id")
    author = ((comment.get("user") or {}).get("login")) or "unknown"
    path = comment.get("path") or "unknown-path"
    body = _squash(comment.get("body"))
    url = comment.get("html_url") or ""
    return f"- review_comment #{comment_id} by {author} on {path}\n  {body}\n  {url}"


def _format_issue_comment(comment: dict[str, Any]) -> str:
    comment_id = comment.get("id")
    author = ((comment.get("user") or {}).get("login")) or "unknown"
    body = _squash(comment.get("body"))
    url = comment.get("html_url") or ""
    return f"- issue_comment #{comment_id} by {author}\n  {body}\n  {url}"


def _fetch_state(repo: str, pr_number: int, token: str | None) -> dict[str, list[dict[str, Any]]]:
    encoded_repo = urllib.parse.quote(repo, safe="/")
    reviews = _list_paginated(f"https://api.github.com/repos/{encoded_repo}/pulls/{pr_number}/reviews", token)
    review_comments = _list_paginated(
        f"https://api.github.com/repos/{encoded_repo}/pulls/{pr_number}/comments",
        token,
    )
    issue_comments = _list_paginated(
        f"https://api.github.com/repos/{encoded_repo}/issues/{pr_number}/comments",
        token,
    )
    return {
        "reviews": reviews,
        "review_comments": review_comments,
        "issue_comments": issue_comments,
    }


def _requests_per_poll(state: dict[str, list[dict[str, Any]]]) -> int:
    requests = 0
    for key in ("reviews", "review_comments", "issue_comments"):
        item_count = len(state[key])
        requests += max(1, (item_count // 100) + 1)
    return requests


def _render_activity(
    *,
    repo: str,
    pr_number: int,
    state: dict[str, list[dict[str, Any]]],
    review_cursor: int,
    review_comment_cursor: int,
    issue_comment_cursor: int,
    event_limit: int,
) -> tuple[str | None, int, int, int]:
    new_reviews = _filter_new(state["reviews"], review_cursor)
    new_review_comments = _filter_new(state["review_comments"], review_comment_cursor)
    new_issue_comments = _filter_new(state["issue_comments"], issue_comment_cursor)

    if not (new_reviews or new_review_comments or new_issue_comments):
        return None, review_cursor, review_comment_cursor, issue_comment_cursor

    next_review_cursor = max(review_cursor, _max_id(new_reviews))
    next_review_comment_cursor = max(review_comment_cursor, _max_id(new_review_comments))
    next_issue_comment_cursor = max(issue_comment_cursor, _max_id(new_issue_comments))

    lines = [f"GitHub PR activity detected for {repo}#{pr_number}"]
    rendered_events: list[str] = []
    rendered_events.extend(_format_review(review) for review in new_reviews)
    rendered_events.extend(_format_review_comment(comment) for comment in new_review_comments)
    rendered_events.extend(_format_issue_comment(comment) for comment in new_issue_comments)

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
    )


def _write_cursor_output(
    path: str | None,
    *,
    review_cursor: int,
    review_comment_cursor: int,
    issue_comment_cursor: int,
) -> None:
    if not path:
        return

    payload = {
        "review_cursor": review_cursor,
        "review_comment_cursor": review_comment_cursor,
        "issue_comment_cursor": issue_comment_cursor,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub repo in owner/name form")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
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
    parser.add_argument("--cursor-output", help=argparse.SUPPRESS)
    parser.add_argument("--event-limit", type=int, default=8, help="Maximum number of new events to include in stdout")
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

    token = _get_token()
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

    effective_interval = max(args.interval, 1.0)

    start = time.monotonic()

    try:
        state = _fetch_state(args.repo, args.pr, token)
    except urllib.error.HTTPError as err:
        print(f"GitHub API error: {err.code} {err.reason}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001
        print(f"Failed to fetch initial PR state: {err}", file=sys.stderr)
        return 1

    if token is None:
        requests_per_poll = _requests_per_poll(state)
        bootstrap_requests = requests_per_poll
        unauthenticated_min = _min_interval_for_unauthenticated(
            requests_per_poll,
            bootstrap_requests=bootstrap_requests,
        )
        if effective_interval < unauthenticated_min:
            print(
                (
                    "No GitHub token detected; clamping polling interval from %.1fs to %.1fs "
                    "for %s request(s) per poll plus %s bootstrap request(s) to avoid "
                    "unauthenticated rate-limit lockout."
                )
                % (effective_interval, unauthenticated_min, requests_per_poll, bootstrap_requests),
                file=sys.stderr,
            )
            effective_interval = unauthenticated_min
    else:
        bootstrap_requests = 0

    review_cursor = (
        args.since_review_id
        if args.since_review_id is not None
        else (0 if args.catch_up else _max_id(state["reviews"]))
    )
    review_comment_cursor = (
        args.since_review_comment_id
        if args.since_review_comment_id is not None
        else (0 if args.catch_up else _max_id(state["review_comments"]))
    )
    issue_comment_cursor = (
        args.since_issue_comment_id
        if args.since_issue_comment_id is not None
        else (0 if args.catch_up else _max_id(state["issue_comments"]))
    )

    print(
        (
            "Watching GitHub PR %s#%s from cursors: review=%s review_comment=%s issue_comment=%s catch_up=%s"
            % (args.repo, args.pr, review_cursor, review_comment_cursor, issue_comment_cursor, args.catch_up)
        ),
        file=sys.stderr,
    )

    initial_output, review_cursor, review_comment_cursor, issue_comment_cursor = _render_activity(
        repo=args.repo,
        pr_number=args.pr,
        state=state,
        review_cursor=review_cursor,
        review_comment_cursor=review_comment_cursor,
        issue_comment_cursor=issue_comment_cursor,
        event_limit=args.event_limit,
    )
    if initial_output is not None:
        _write_cursor_output(
            args.cursor_output,
            review_cursor=review_cursor,
            review_comment_cursor=review_comment_cursor,
            issue_comment_cursor=issue_comment_cursor,
        )
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
            state = _fetch_state(args.repo, args.pr, token)
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
            requests_per_poll = _requests_per_poll(state)
            unauthenticated_min = _min_interval_for_unauthenticated(
                requests_per_poll,
                bootstrap_requests=bootstrap_requests,
            )
            if effective_interval < unauthenticated_min:
                print(
                    (
                        "GitHub unauthenticated polling now needs %.1fs minimum for %s request(s) "
                        "per poll plus %s bootstrap request(s); increasing interval."
                    )
                    % (unauthenticated_min, requests_per_poll, bootstrap_requests),
                    file=sys.stderr,
                )
                effective_interval = unauthenticated_min

        output, review_cursor, review_comment_cursor, issue_comment_cursor = _render_activity(
            repo=args.repo,
            pr_number=args.pr,
            state=state,
            review_cursor=review_cursor,
            review_comment_cursor=review_comment_cursor,
            issue_comment_cursor=issue_comment_cursor,
            event_limit=args.event_limit,
        )
        if output is None:
            continue

        _write_cursor_output(
            args.cursor_output,
            review_cursor=review_cursor,
            review_comment_cursor=review_comment_cursor,
            issue_comment_cursor=issue_comment_cursor,
        )
        print(output)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
