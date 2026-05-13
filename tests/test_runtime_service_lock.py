import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import runtime


class RuntimeServiceLockTests(unittest.TestCase):
    def test_start_service_reuses_existing_live_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("12345", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch(
                        "vibe.runtime.get_process_command",
                        return_value=f"{sys.executable} {runtime.get_service_main_path()}",
                    ):
                        with patch("vibe.runtime.spawn_background") as spawn_background:
                            pid = runtime.start_service()

            self.assertEqual(pid, 12345)
            spawn_background.assert_not_called()

    def test_start_service_ignores_reused_unrelated_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("12345", encoding="utf-8")

            def fake_spawn(args, target_pid_path, stdout_name, stderr_name, env=None):
                target_pid_path.write_text("67890", encoding="utf-8")
                return 67890

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch("vibe.runtime.get_process_command", return_value="/usr/bin/unrelated --work"):
                        with patch("vibe.runtime.spawn_background", side_effect=fake_spawn) as spawn_background:
                            pid = runtime.start_service()

            self.assertEqual(pid, 67890)
            spawn_background.assert_called_once()
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "67890")

    def test_start_service_reuses_live_pid_when_command_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("12345", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch("vibe.runtime.get_process_command", return_value=None):
                        with patch("vibe.runtime.spawn_background") as spawn_background:
                            pid = runtime.start_service()

            self.assertEqual(pid, 12345)
            spawn_background.assert_not_called()

if __name__ == "__main__":
    unittest.main()
