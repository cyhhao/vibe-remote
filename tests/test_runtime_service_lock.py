import os
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

            def fake_spawn(args, stdout_name, stderr_name, env=None):
                return 67890

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch("vibe.runtime.get_process_command", return_value="/usr/bin/unrelated --work"):
                        with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                            with patch("vibe.runtime.spawn_service_background", side_effect=fake_spawn) as spawn_background:
                                with patch("vibe.runtime.wait_for_service_pid", return_value=True):
                                    pid = runtime.start_service()

            self.assertEqual(pid, 67890)
            spawn_background.assert_called_once()
            self.assertFalse(pid_path.exists())

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

    def test_start_service_errors_when_lock_holder_is_not_recorded_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(False, 12345)):
                    with patch("vibe.runtime.pid_alive", return_value=True):
                        with patch("vibe.runtime.spawn_service_background") as spawn_background:
                            with self.assertRaises(runtime.ServiceAlreadyRunningError):
                                runtime.start_service()

            spawn_background.assert_not_called()

    def test_start_service_errors_when_spawned_process_never_acquires_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                    with patch("vibe.runtime.spawn_service_background", return_value=67890):
                        with patch("vibe.runtime.wait_for_service_pid", return_value=False):
                            with self.assertRaises(RuntimeError):
                                runtime.start_service()

    def test_service_instance_lock_blocks_second_holder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir) / "runtime"
            runtime_dir.mkdir(parents=True)
            lock_path = runtime_dir / "service.lock"
            pid_path = runtime_dir / "vibe.pid"

            with patch("vibe.runtime.paths.get_runtime_service_lock_path", return_value=lock_path):
                with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                    with patch("vibe.runtime.paths.ensure_data_dirs", return_value=None):
                        runtime.acquire_service_instance_lock()
                        try:
                            available, holder_pid = runtime.service_instance_lock_available()
                        finally:
                            runtime.release_service_instance_lock()

            self.assertFalse(available)
            self.assertEqual(holder_pid, os.getpid())
            self.assertFalse(pid_path.exists())

if __name__ == "__main__":
    unittest.main()
