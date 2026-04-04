from __future__ import annotations

import os
from datetime import datetime

from config import paths
from vibe.ui_server import app
from vibe import runtime
from tests.ui_server_test_helpers import csrf_headers


def _set_mtime(path, timestamp: str) -> None:
    value = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").timestamp()
    os.utime(path, (value, value))


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
    response = client.post("/logs", json={"lines": 20, "source": "ui_stderr"}, headers=csrf_headers(client))

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

    service_log = paths.get_logs_dir() / "vibe_remote.log"
    service_log.write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - main service failed\n",
        encoding="utf-8",
    )
    service_stderr_log = paths.get_runtime_dir() / "service_stderr.log"
    service_stderr_log.write_text(
        "service stderr line\n",
        encoding="utf-8",
    )
    ui_stderr_log = paths.get_runtime_dir() / "ui_stderr.log"
    ui_stderr_log.write_text(
        "UI boot failed\n",
        encoding="utf-8",
    )
    _set_mtime(service_stderr_log, "2026-03-25 15:51:18")
    _set_mtime(ui_stderr_log, "2026-03-25 15:51:19")

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "all"}, headers=csrf_headers(client))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 3
    assert [entry["source"] for entry in payload["logs"]] == [
        "service",
        "service_stderr",
        "ui_stderr",
    ]
    assert {entry["source"] for entry in payload["logs"]} == {"service", "service_stderr", "ui_stderr"}


def test_logs_endpoint_caps_aggregated_all_view_to_requested_lines(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "\n".join(
            [
                "2026-03-25 15:51:17,428 - service.main - INFO - service line 1",
                "2026-03-25 15:51:18,428 - service.main - INFO - service line 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (paths.get_runtime_dir() / "service_stderr.log").write_text(
        "\n".join(
            [
                "2026-03-25 15:51:19,428 - service.stderr - ERROR - service stderr line 1",
                "2026-03-25 15:51:20,428 - service.stderr - ERROR - service stderr line 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (paths.get_runtime_dir() / "ui_stderr.log").write_text(
        "\n".join(
            [
                "2026-03-25 15:51:21,428 - ui.stderr - ERROR - ui stderr line 1",
                "2026-03-25 15:51:22,428 - ui.stderr - ERROR - ui stderr line 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/logs", json={"lines": 2, "source": "all"}, headers=csrf_headers(client))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 6
    assert len(payload["logs"]) == 2
    assert [entry["message"] for entry in payload["logs"]] == [
        "ui stderr line 1",
        "ui stderr line 2",
    ]


def test_logs_endpoint_keeps_traceback_exception_summary_with_error_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - Task was destroyed but it is pending!\n"
        "Traceback (most recent call last):\n"
        '  File "/app/core/update_checker.py", line 222, in _check_loop\n'
        "ValueError: boom\n",
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "service"}, headers=csrf_headers(client))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "service"
    assert len(payload["logs"]) == 1
    assert payload["logs"][0]["level"] == "ERROR"
    assert payload["logs"][0]["message"].endswith("ValueError: boom")


def test_logs_endpoint_preserves_recent_unstructured_logs_in_all_view(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    service_log = paths.get_logs_dir() / "vibe_remote.log"
    service_log.write_text(
        "\n".join(
            [
                "2026-03-25 15:51:17,428 - service.main - INFO - service line 1",
                "2026-03-25 15:51:18,428 - service.main - INFO - service line 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ui_stderr_log = paths.get_runtime_dir() / "ui_stderr.log"
    ui_stderr_log.write_text("latest ui crash line\n", encoding="utf-8")
    _set_mtime(ui_stderr_log, "2026-03-25 15:51:19")

    client = app.test_client()
    response = client.post("/logs", json={"lines": 2, "source": "all"}, headers=csrf_headers(client))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 3
    assert [entry["message"] for entry in payload["logs"]] == [
        "service line 2",
        "latest ui crash line",
    ]


def test_logs_endpoint_falls_back_to_service_for_unknown_source(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    (paths.get_logs_dir() / "vibe_remote.log").write_text(
        "2026-03-25 15:51:17,428 - asyncio - ERROR - [base_events.py:1875] - Task was destroyed but it is pending!\n",
        encoding="utf-8",
    )

    client = app.test_client()
    response = client.post("/logs", json={"lines": 20, "source": "unknown"}, headers=csrf_headers(client))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "all"
    assert payload["total"] == 1
    assert payload["logs"][0]["logger"] == "asyncio"


def test_status_endpoint_degrades_when_pid_probe_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    paths.get_runtime_pid_path().write_text("12345", encoding="utf-8")
    runtime.write_status("running", detail="pid=12345", service_pid=12345)

    def _raise(_pid):
        raise SystemError("boom")

    monkeypatch.setattr(runtime, "pid_alive", _raise)

    client = app.test_client()
    response = client.get("/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["running"] is False
    assert payload["pid"] is None
    assert payload["state"] == "stopped"
