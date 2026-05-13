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

    def test_runtime_processes_stale_when_service_predates_installed_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pid_path = root / "service.pid"
            ui_pid_path = root / "ui.pid"
            package_root = root / "site-packages" / "vibe"
            project_root = root / "site-packages"
            package_root.mkdir(parents=True)
            init_path = package_root / "__init__.py"
            service_main = package_root / "service_main.py"
            ui_server = package_root / "ui_server.py"
            for marker in (init_path, service_main, ui_server):
                marker.write_text("", encoding="utf-8")
                os.utime(marker, (2000, 2000))
            pid_path.write_text("12345", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.paths.get_runtime_ui_pid_path", return_value=ui_pid_path):
                    with patch("vibe.runtime.get_project_root", return_value=project_root):
                        with patch("vibe.runtime.get_package_root", return_value=package_root):
                            with patch("vibe.runtime.get_service_main_path", return_value=service_main):
                                with patch("vibe.runtime.pid_alive", return_value=True):
                                    with patch(
                                        "vibe.runtime.get_process_command",
                                        return_value=f"{sys.executable} {service_main}",
                                    ):
                                        with patch("vibe.runtime.get_process_start_time", return_value=1000.0):
                                            self.assertTrue(runtime.runtime_processes_stale_after_package_update())

    def test_runtime_process_stale_check_is_disabled_in_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pid_path = root / "service.pid"
            ui_pid_path = root / "ui.pid"
            project_root = root / "checkout"
            package_root = project_root / "vibe"
            package_root.mkdir(parents=True)
            (project_root / "main.py").write_text("", encoding="utf-8")
            service_main = project_root / "main.py"
            (package_root / "__init__.py").write_text("", encoding="utf-8")
            (package_root / "ui_server.py").write_text("", encoding="utf-8")
            pid_path.write_text("12345", encoding="utf-8")

            with patch("vibe.runtime.paths.get_runtime_pid_path", return_value=pid_path):
                with patch("vibe.runtime.paths.get_runtime_ui_pid_path", return_value=ui_pid_path):
                    with patch("vibe.runtime.get_project_root", return_value=project_root):
                        with patch("vibe.runtime.get_package_root", return_value=package_root):
                            with patch("vibe.runtime.get_service_main_path", return_value=service_main):
                                with patch("vibe.runtime.pid_alive", return_value=True):
                                    with patch(
                                        "vibe.runtime.get_process_command",
                                        return_value=f"{sys.executable} {service_main}",
                                    ):
                                        with patch("vibe.runtime.get_process_start_time", return_value=1000.0):
                                            self.assertFalse(runtime.runtime_processes_stale_after_package_update())


if __name__ == "__main__":
    unittest.main()
