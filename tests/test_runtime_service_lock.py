import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
                        with patch("vibe.runtime.service_pid_recorded", return_value=True):
                            with patch("vibe.runtime.spawn_service_background_process") as spawn_background:
                                pid = runtime.start_service()

            self.assertEqual(pid, 12345)
            spawn_background.assert_not_called()

    def test_start_service_ignores_reused_unrelated_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("12345", encoding="utf-8")

            def fake_spawn(args, stdout_name, stderr_name, env=None):
                return SimpleNamespace(pid=67890, poll=lambda: None)

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch("vibe.runtime.get_process_command", return_value="/usr/bin/unrelated --work"):
                        with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                            with patch(
                                "vibe.runtime.spawn_service_background_process", side_effect=fake_spawn
                            ) as spawn_background:
                                with patch("vibe.runtime.wait_for_service_pid", return_value=True):
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
                        with patch("vibe.runtime.service_pid_recorded", return_value=True):
                            with patch("vibe.runtime.spawn_service_background_process") as spawn_background:
                                pid = runtime.start_service()

            self.assertEqual(pid, 12345)
            spawn_background.assert_not_called()

    def test_start_service_errors_when_lock_holder_is_not_recorded_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(False, 12345)):
                    with patch("vibe.runtime.pid_alive", return_value=True):
                        with patch("vibe.runtime.spawn_service_background_process") as spawn_background:
                            with self.assertRaises(runtime.ServiceAlreadyRunningError):
                                runtime.start_service()

            spawn_background.assert_not_called()

    def test_start_service_returns_live_pid_when_lock_write_is_slow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            process = SimpleNamespace(pid=67890, poll=lambda: None)

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                    with patch("vibe.runtime.spawn_service_background_process", return_value=process):
                        with patch("vibe.runtime.wait_for_service_pid", return_value=False):
                            with patch("vibe.runtime.pid_alive", return_value=True):
                                pid = runtime.start_service(wait_for_ready=False)

            self.assertEqual(pid, 67890)
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "67890")

    def test_start_service_can_skip_initial_ready_wait(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            process = SimpleNamespace(pid=67890, poll=lambda: None)

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                    with patch("vibe.runtime.spawn_service_background_process", return_value=process):
                        with patch("vibe.runtime.wait_for_service_pid", return_value=True) as wait_for_pid:
                            with patch("vibe.runtime.pid_alive", return_value=True):
                                pid = runtime.start_service(wait_for_ready=False, initial_ready_timeout=0)

            self.assertEqual(pid, 67890)
            wait_for_pid.assert_not_called()
            self.assertEqual(pid_path.read_text(encoding="utf-8"), "67890")

    def test_start_service_errors_when_spawned_process_dies_before_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            process = SimpleNamespace(pid=67890, poll=lambda: 1)

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                    with patch("vibe.runtime.spawn_service_background_process", return_value=process):
                        with patch("vibe.runtime.wait_for_service_pid", return_value=False):
                            with patch("vibe.runtime.pid_alive", return_value=True):
                                with self.assertRaises(RuntimeError):
                                    runtime.start_service()

            self.assertFalse(pid_path.exists())

    def test_start_service_waits_for_readiness_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            process = SimpleNamespace(pid=67890, poll=lambda: None)

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_instance_lock_available", return_value=(True, None)):
                    with patch("vibe.runtime.spawn_service_background_process", return_value=process):
                        with patch("vibe.runtime.wait_for_service_pid", side_effect=[False, True]) as wait_for_pid:
                            with patch("vibe.runtime.pid_alive", return_value=True):
                                pid = runtime.start_service()

            self.assertEqual(pid, 67890)
            self.assertEqual(wait_for_pid.call_count, 2)

    def test_start_service_reuses_pending_reservation_without_spawning_second_worker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("67890", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch(
                        "vibe.runtime.get_process_command",
                        return_value=f"{sys.executable} {runtime.get_service_main_path()}",
                    ):
                        with patch("vibe.runtime.service_pid_recorded", return_value=False):
                            with patch("vibe.runtime.spawn_service_background_process") as spawn_background:
                                pid = runtime.start_service(wait_for_ready=False)

            self.assertEqual(pid, 67890)
            spawn_background.assert_not_called()

    def test_stop_service_stops_pending_pid_reservation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            pid_path.write_text("67890", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.pid_alive", return_value=True):
                    with patch("vibe.runtime.stop_pid", return_value=True) as stop_pid:
                        self.assertTrue(runtime.stop_service())

            stop_pid.assert_called_once_with(67890, timeout=5)
            self.assertFalse(pid_path.exists())

    def test_wait_for_service_pid_adopts_slow_pid_file_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"
            calls = []

            def fake_service_pid_recorded(pid):
                calls.append(pid)
                if len(calls) == 2:
                    pid_path.write_text(str(pid), encoding="utf-8")
                    return True
                return False

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_pid_recorded", side_effect=fake_service_pid_recorded):
                    with patch("vibe.runtime.pid_alive", return_value=True):
                        with patch("vibe.runtime.time.sleep", return_value=None):
                            self.assertTrue(runtime.wait_for_service_pid(67890, timeout=1.0))

            self.assertEqual(calls, [67890, 67890])

    def test_wait_for_service_pid_fails_only_when_worker_dies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "service.pid"

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.service_pid_recorded", return_value=False):
                    with patch("vibe.runtime.pid_alive", return_value=False):
                        self.assertFalse(runtime.wait_for_service_pid(67890, timeout=1.0))

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
