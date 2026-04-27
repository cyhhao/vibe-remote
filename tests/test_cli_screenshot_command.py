import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import cli
from vibe.screenshot import ScreenshotError, ScreenshotResult, capture_screenshot


def test_screenshot_parser_accepts_output_and_json() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["screenshot", "--output", "screen.png", "--json"])

    assert args.command == "screenshot"
    assert args.output == "screen.png"
    assert args.json is True


def test_cmd_screenshot_prints_path(capsys, monkeypatch) -> None:
    captured: list[str | None] = []

    def fake_capture(output=None):
        captured.append(output)
        return ScreenshotResult(path=Path("/tmp/vibe-screen.png"), backend="fake")

    monkeypatch.setattr(cli, "capture_screenshot", fake_capture)
    parser = cli.build_parser()
    args = parser.parse_args(["screenshot", "--output", "custom.png"])

    assert cli.cmd_screenshot(args) == 0
    assert captured == ["custom.png"]
    assert capsys.readouterr().out.strip() == "/tmp/vibe-screen.png"


def test_cmd_screenshot_prints_json(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "capture_screenshot",
        lambda output=None: ScreenshotResult(path=Path("/tmp/vibe-screen.png"), backend="fake"),
    )
    parser = cli.build_parser()
    args = parser.parse_args(["screenshot", "--json"])

    assert cli.cmd_screenshot(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "path": "/tmp/vibe-screen.png",
        "backend": "fake",
    }


def test_cmd_screenshot_reports_errors(capsys, monkeypatch) -> None:
    def fake_capture(output=None):
        raise ScreenshotError("display is unavailable")

    monkeypatch.setattr(cli, "capture_screenshot", fake_capture)
    parser = cli.build_parser()
    args = parser.parse_args(["screenshot", "--json"])

    assert cli.cmd_screenshot(args) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["code"] == "screenshot_failed"
    assert payload["error"] == "display is unavailable"


def test_capture_screenshot_wraps_output_preparation_errors(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")

    try:
        capture_screenshot(blocked_parent / "screen.png")
    except ScreenshotError as exc:
        assert "failed to prepare screenshot output path" in str(exc)
    else:
        raise AssertionError("expected ScreenshotError")
