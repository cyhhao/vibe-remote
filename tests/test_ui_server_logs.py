from __future__ import annotations

from config import paths
from vibe.ui_server import app


def test_logs_endpoint_returns_multiple_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - [base_events.py:1875] - Task was destroyed but it is pending!\n"
        "traceback line\n",
        encoding="utf-8",
    )
    (paths.get_runtime_dir() / "service_stderr.log").write_text("service stderr line\n", encoding="utf-8")
    (paths.get_runtime_dir() / "ui_stderr.log").write_text("UI boot failed\nTraceback line\n", encoding="utf-8")

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "ui_stderr"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "ui_stderr"
    assert payload["total"] == 2
    assert isinstance(payload["logs"], list)
    assert {source["key"] for source in payload["sources"]} == {
        "all",
        "service",
        "service_stdout",
        "service_stderr",
        "ui_stdout",
        "ui_stderr",
    }
    assert next(source for source in payload["sources"] if source["key"] == "service")["total"] == 2
    assert next(source for source in payload["sources"] if source["key"] == "all")["total"] == 5
    assert payload["logs"][0]["logger"] == "ui_stderr"
    assert payload["logs"][0]["message"] == "UI boot failed\nTraceback line"
    assert payload["logs"][0]["source"] == "ui_stderr"


def test_logs_endpoint_returns_aggregated_all_view(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - main service failed\n",
        encoding="utf-8",
    )
    (paths.get_runtime_dir() / "service_stderr.log").write_text(
        "service stderr line\n",
        encoding="utf-8",
    )
    (paths.get_runtime_dir() / "ui_stderr.log").write_text(
        "UI boot failed\n",
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "all"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 3
    assert [entry["source"] for entry in payload["logs"]] == [
        "service_stderr",
        "ui_stderr",
        "service",
    ]
    assert {entry["source"] for entry in payload["logs"]} == {"service", "service_stderr", "ui_stderr"}


def test_logs_endpoint_falls_back_to_service_for_unknown_source(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - [base_events.py:1875] - Task was destroyed but it is pending!\n",
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "unknown"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 1
    assert payload["logs"][0]["logger"] == "asyncio"
