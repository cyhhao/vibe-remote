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
        / "wait_issue.py"
    )
    spec = importlib.util.spec_from_file_location("wait_issue", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_render_new_issues_ignores_pull_requests() -> None:
    module = _load_module()
    state = {
        "issues": [
            {
                "id": 201,
                "number": 24,
                "title": "New issue",
                "state": "open",
                "html_url": "https://github.com/example/repo/issues/24",
                "user": {"login": "someone"},
            }
        ]
    }

    output, issue_cursor = module._render_new_issues(
        repo="cyhhao/vibe-remote",
        state=state,
        issue_cursor=0,
        event_limit=8,
    )

    assert output is not None
    assert "issue #24" in output
    assert issue_cursor == 201


def test_render_issue_comments_includes_new_comment() -> None:
    module = _load_module()
    state = {
        "issue_comments": [
            {
                "id": 301,
                "body": "Need more logs",
                "html_url": "https://github.com/example/repo/issues/24#issuecomment-301",
                "user": {"login": "reviewer"},
            }
        ]
    }

    output, issue_comment_cursor = module._render_issue_comments(
        repo="cyhhao/vibe-remote",
        issue_number=24,
        state=state,
        issue_comment_cursor=0,
        event_limit=8,
    )

    assert output is not None
    assert "issue_comment #301" in output
    assert "issue #24" in output
    assert issue_comment_cursor == 301


def test_render_issue_comments_ignores_self_authored_comment_but_advances_cursor() -> None:
    module = _load_module()
    state = {
        "issue_comments": [
            {
                "id": 302,
                "body": "I will take this",
                "html_url": "https://github.com/example/repo/issues/24#issuecomment-302",
                "user": {"login": "cyhhao"},
            }
        ]
    }

    output, issue_comment_cursor = module._render_issue_comments(
        repo="cyhhao/vibe-remote",
        issue_number=24,
        state=state,
        issue_comment_cursor=0,
        event_limit=8,
        viewer_login="cyhhao",
    )

    assert output is None
    assert issue_comment_cursor == 302


def test_fetch_new_issue_state_tracks_raw_cursor_and_request_count() -> None:
    module = _load_module()
    raw_items = [
        {
            "id": 401,
            "number": 41,
            "title": "PR disguised by /issues",
            "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/example/repo/pulls/41"},
        },
        {
            "id": 400,
            "number": 40,
            "title": "Real issue",
            "state": "open",
        },
    ]

    def _fake_list_paginated_with_count(base_url, token, *, stop_after_id=None):
        assert "issues?state=all" in base_url
        assert stop_after_id == 401
        return raw_items, 3

    with patch.object(module, "list_paginated_with_count", side_effect=_fake_list_paginated_with_count):
        state, request_count = module._fetch_new_issue_state(
            "cyhhao/vibe-remote",
            token="token",
            stop_after_id=401,
        )

    assert len(state["issues"]) == 1
    assert state["issues"][0]["id"] == 400
    assert state["raw_issue_cursor"] == 401
    assert request_count == 3


def test_main_uses_raw_issue_cursor_for_new_issue_paging() -> None:
    module = _load_module()
    calls: list[int | None] = []

    def _fake_fetch_new_issue_state(repo, token, *, stop_after_id=None):
        calls.append(stop_after_id)
        if len(calls) == 1:
            return (
                {
                    "issues": [
                        {
                            "id": 400,
                            "number": 40,
                            "title": "Existing issue",
                            "state": "open",
                            "html_url": "https://github.com/example/repo/issues/40",
                            "user": {"login": "someone"},
                        }
                    ],
                    "raw_issue_cursor": 401,
                },
                2,
            )
        assert stop_after_id == 401
        return (
            {
                "issues": [
                    {
                        "id": 405,
                        "number": 41,
                        "title": "New issue",
                        "state": "open",
                        "html_url": "https://github.com/example/repo/issues/41",
                        "user": {"login": "someone"},
                    }
                ],
                "raw_issue_cursor": 406,
            },
            1,
        )

    stdout = io.StringIO()
    with (
        patch.object(module, "_fetch_new_issue_state", side_effect=_fake_fetch_new_issue_state),
        patch.object(module, "get_token", return_value="token"),
        patch.object(module, "get_authenticated_login", return_value=None),
        patch.object(module.time, "sleep", return_value=None),
        patch("sys.argv", ["wait_issue.py", "--repo", "cyhhao/vibe-remote", "--new-issues", "--interval", "1"]),
        redirect_stdout(stdout),
    ):
        rc = module.main()

    assert rc == 0
    assert calls == [None, 401]
    assert "issue #41" in stdout.getvalue()


def test_main_uses_since_raw_issue_cursor_for_initial_new_issue_fetch() -> None:
    module = _load_module()
    calls: list[int | None] = []

    def _fake_fetch_new_issue_state(repo, token, *, stop_after_id=None):
        calls.append(stop_after_id)
        return (
            {
                "issues": [
                    {
                        "id": 405,
                        "number": 41,
                        "title": "New issue",
                        "state": "open",
                        "html_url": "https://github.com/example/repo/issues/41",
                        "user": {"login": "someone"},
                    }
                ],
                "raw_issue_cursor": 406,
            },
            1,
        )

    stdout = io.StringIO()
    with (
        patch.object(module, "_fetch_new_issue_state", side_effect=_fake_fetch_new_issue_state),
        patch.object(module, "get_token", return_value="token"),
        patch.object(module, "get_authenticated_login", return_value=None),
        patch(
            "sys.argv",
            [
                "wait_issue.py",
                "--repo",
                "cyhhao/vibe-remote",
                "--new-issues",
                "--since-issue-id",
                "400",
                "--since-raw-issue-id",
                "401",
            ],
        ),
        redirect_stdout(stdout),
    ):
        rc = module.main()

    assert rc == 0
    assert calls == [401]
    assert "issue #41" in stdout.getvalue()
