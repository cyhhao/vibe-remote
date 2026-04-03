from __future__ import annotations

import io
import importlib.util
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "wait_pr.py"
    )
    spec = importlib.util.spec_from_file_location("wait_pr", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_render_activity_includes_codex_pr_body_reaction() -> None:
    module = _load_module()
    state = {
        "reviews": [],
        "review_comments": [],
        "issue_comments": [],
        "reactions": [
            {
                "id": 123,
                "content": "+1",
                "created_at": "2026-04-02T13:05:42Z",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            }
        ],
    }

    output, review_cursor, review_comment_cursor, issue_comment_cursor, reaction_cursor = module._render_activity(
        repo="cyhhao/vibe-remote",
        pr_number=153,
        state=state,
        review_cursor=0,
        review_comment_cursor=0,
        issue_comment_cursor=0,
        reaction_cursor=0,
        event_limit=8,
    )

    assert output is not None
    assert "pr_reaction #123" in output
    assert "chatgpt-codex-connector[bot]" in output
    assert reaction_cursor == 123
    assert review_cursor == 0
    assert review_comment_cursor == 0
    assert issue_comment_cursor == 0


def test_render_activity_ignores_non_codex_or_non_plus_one_reactions() -> None:
    module = _load_module()
    state = {
        "reviews": [],
        "review_comments": [],
        "issue_comments": [],
        "reactions": [
            {
                "id": 124,
                "content": "heart",
                "created_at": "2026-04-02T13:05:42Z",
                "user": {"login": "chatgpt-codex-connector[bot]"},
            },
            {
                "id": 125,
                "content": "+1",
                "created_at": "2026-04-02T13:05:42Z",
                "user": {"login": "someone-else"},
            },
        ],
    }

    output, *_rest = module._render_activity(
        repo="cyhhao/vibe-remote",
        pr_number=153,
        state=state,
        review_cursor=0,
        review_comment_cursor=0,
        issue_comment_cursor=0,
        reaction_cursor=0,
        event_limit=8,
    )

    assert output is None


def test_render_activity_ignores_self_authored_issue_comment_but_advances_cursor() -> None:
    module = _load_module()
    state = {
        "reviews": [],
        "review_comments": [],
        "issue_comments": [
            {
                "id": 126,
                "body": "  @CoDeX ReViEw  ",
                "html_url": "https://github.com/example/repo/pull/1#issuecomment-126",
                "user": {"login": "someone"},
            }
        ],
        "reactions": [],
    }

    output, review_cursor, review_comment_cursor, issue_comment_cursor, reaction_cursor = module._render_activity(
        repo="cyhhao/vibe-remote",
        pr_number=153,
        state=state,
        review_cursor=0,
        review_comment_cursor=0,
        issue_comment_cursor=0,
        reaction_cursor=0,
        event_limit=8,
        viewer_login="someone",
    )

    assert output is None
    assert review_cursor == 0
    assert review_comment_cursor == 0
    assert issue_comment_cursor == 126
    assert reaction_cursor == 0


def test_render_activity_includes_self_authored_comment_when_disabled() -> None:
    module = _load_module()
    state = {
        "reviews": [],
        "review_comments": [],
        "issue_comments": [
            {
                "id": 127,
                "body": "@codex review",
                "html_url": "https://github.com/example/repo/pull/1#issuecomment-127",
                "user": {"login": "someone"},
            }
        ],
        "reactions": [],
    }

    output, *_rest = module._render_activity(
        repo="cyhhao/vibe-remote",
        pr_number=153,
        state=state,
        review_cursor=0,
        review_comment_cursor=0,
        issue_comment_cursor=0,
        reaction_cursor=0,
        event_limit=8,
        viewer_login="someone",
        ignore_self_comments=False,
    )

    assert output is not None
    assert "issue_comment #127" in output


def test_render_new_pull_requests_includes_new_prs() -> None:
    module = _load_module()
    state = {
        "pull_requests": [
            {
                "id": 401,
                "number": 157,
                "title": "feat: add codex subagent routing",
                "state": "open",
                "html_url": "https://github.com/example/repo/pull/157",
                "user": {"login": "cyhhao"},
            }
        ]
    }

    output, pr_cursor = module._render_new_pull_requests(
        repo="cyhhao/vibe-remote",
        state=state,
        pr_cursor=0,
        event_limit=8,
    )

    assert output is not None
    assert "pull_request #157" in output
    assert pr_cursor == 401


def test_fetch_new_pr_state_stops_after_cursor() -> None:
    module = _load_module()
    responses = [
        [
            {"id": 410, "number": 2, "title": "Newest", "state": "open"},
            {"id": 405, "number": 1, "title": "Known", "state": "open"},
        ]
    ]

    def _fake_list_paginated_with_count(base_url, token, *, stop_after_id=None):
        assert "pulls?state=all" in base_url
        assert stop_after_id == 405
        return responses[0], 1

    with patch.object(module, "list_paginated_with_count", side_effect=_fake_list_paginated_with_count):
        state, request_count = module._fetch_new_pr_state(
            "cyhhao/vibe-remote",
            token="token",
            stop_after_id=405,
        )

    assert state["pull_requests"][0]["id"] == 410
    assert request_count == 1


def test_main_uses_since_pr_cursor_for_initial_new_pr_fetch() -> None:
    module = _load_module()
    calls: list[int | None] = []

    def _fake_fetch_new_pr_state(repo, token, *, stop_after_id=None):
        calls.append(stop_after_id)
        return (
            {
                "pull_requests": [
                    {
                        "id": 410,
                        "number": 158,
                        "title": "New PR",
                        "state": "open",
                        "html_url": "https://github.com/example/repo/pull/158",
                        "user": {"login": "cyhhao"},
                    }
                ]
            },
            1,
        )

    stdout = io.StringIO()
    with (
        patch.object(module, "_fetch_new_pr_state", side_effect=_fake_fetch_new_pr_state),
        patch.object(module, "get_token", return_value="token"),
        patch.object(module, "get_authenticated_login", return_value=None),
        patch(
            "sys.argv",
            [
                "wait_pr.py",
                "--repo",
                "cyhhao/vibe-remote",
                "--new-prs",
                "--since-pr-id",
                "405",
            ],
        ),
        redirect_stdout(stdout),
    ):
        rc = module.main()

    assert rc == 0
    assert calls == [405]
    assert "pull_request #158" in stdout.getvalue()
