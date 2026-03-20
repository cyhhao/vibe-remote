import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "scripts" / "docker-entrypoint.sh"


class DockerEntrypointSupervisorTests(unittest.TestCase):
    def test_full_mode_exits_when_service_process_dies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fake_python = tmp_path / "python"
            fake_python.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import sys
                    import time

                    args = sys.argv[1:]
                    if args and args[0] == "main.py":
                        sys.exit(42)

                    if len(args) >= 2 and args[0] == "-c":
                        code = args[1]
                        if "run_ui_server" in code:
                            time.sleep(30)
                        sys.exit(0)

                    sys.exit(0)
                    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(fake_python.stat().st_mode | stat.S_IEXEC)

            env = os.environ.copy()
            env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
            env["VIBE_REMOTE_HOME"] = str(tmp_path / "home")

            result = subprocess.run(
                ["bash", str(ENTRYPOINT), "full"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            self.assertIn("Service exited unexpectedly", result.stderr)


if __name__ == "__main__":
    unittest.main()
