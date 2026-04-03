from __future__ import annotations

import importlib.util
from pathlib import Path


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
