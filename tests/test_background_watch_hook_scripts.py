from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_forever_watch_preserves_catch_up_until_success(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_github_pr_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_github_pr_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.write_text(
        watch_script.read_text(encoding="utf-8").replace("retry_delay_seconds=30", "retry_delay_seconds=0.1"),
        encoding="utf-8",
    )
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    count_file = tmp_path / "wrapper-count.txt"
    second_call_file = tmp_path / "second-call.json"

    fake_wrapper = f"""#!/usr/bin/env bash
set -euo pipefail

count_file={str(count_file)!r}
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count="$((count + 1))"
printf '%s' "$count" > "$count_file"

cursor_file=""
args=("$@")
for ((i=0; i<${{#args[@]}}; i++)); do
  if [[ "${{args[$i]}}" == "--" ]]; then
    for ((j=i+1; j<${{#args[@]}}; j++)); do
      if [[ "${{args[$j]}}" == "--cursor-output" ]]; then
        cursor_file="${{args[$((j + 1))]}}"
      fi
    done
    break
  fi
done

if [[ "$count" == "1" ]]; then
  exit 1
fi

python3 - <<'PY' "{str(second_call_file)}" "$count" "$@"
import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
count = int(sys.argv[2])
args = sys.argv[3:]
if count == 2:
    out_path.write_text(
        json.dumps({{"args": args, "saw_catch_up": "--catch-up" in args}}),
        encoding="utf-8",
    )
PY

if [[ -n "$cursor_file" ]]; then
  python3 - <<'PY' "$cursor_file"
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps({{
        "review_cursor": 1,
        "review_comment_cursor": 2,
        "issue_comment_cursor": 3,
    }}),
    encoding="utf-8",
)
PY
fi
exit 0
"""
    _make_executable(script_dir / "watch_then_hook.sh", fake_wrapper)
    _make_executable(
        script_dir / "wait_for_github_pr_activity.py",
        "#!/usr/bin/env python3\nraise SystemExit('should not be executed directly in this test')\n",
    )

    process = subprocess.Popen(
        [
            "bash",
            str(watch_script),
            "--foreground",
            "--forever",
            "--session-key",
            "slack::channel::C123",
            "--repo",
            "cyhhao/vibe-remote",
            "--pr",
            "153",
            "--catch-up",
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )
    deadline = time.time() + 5
    try:
        while time.time() < deadline:
            if second_call_file.exists():
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    stderr_output = process.stderr.read() if process.stderr is not None else ""
    assert second_call_file.exists(), stderr_output
    payload = json.loads(second_call_file.read_text(encoding="utf-8"))
    assert payload["saw_catch_up"] is True


def test_forever_watch_suppresses_per_cycle_timeout_hooks(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_github_pr_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_github_pr_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.write_text(
        watch_script.read_text(encoding="utf-8").replace("retry_delay_seconds=30", "retry_delay_seconds=0.1"),
        encoding="utf-8",
    )
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    calls_file = tmp_path / "calls.jsonl"

    fake_wrapper = f"""#!/usr/bin/env bash
set -euo pipefail

calls_file={str(calls_file)!r}
python3 - <<'PY' "$calls_file" "$@"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
args = sys.argv[2:]
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"args": args}}) + "\\n")
PY

if [[ "$*" == *"GitHub PR watch stopped after reaching its lifetime timeout."* ]]; then
  exit 0
fi

exit 124
"""
    _make_executable(script_dir / "watch_then_hook.sh", fake_wrapper)
    _make_executable(
        script_dir / "wait_for_github_pr_activity.py",
        "#!/usr/bin/env python3\nraise SystemExit('should not be executed directly in this test')\n",
    )

    result = subprocess.run(
        [
            "bash",
            str(watch_script),
            "--foreground",
            "--forever",
            "--session-key",
            "slack::channel::C123",
            "--repo",
            "cyhhao/vibe-remote",
            "--pr",
            "153",
            "--timeout",
            "10",
            "--lifetime-timeout",
            "2",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )

    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in calls_file.read_text(encoding="utf-8").splitlines()]
    assert len(lines) >= 2
    first_args = lines[0]["args"]
    assert "--timeout" in first_args
    assert "0" in first_args
    assert "--timeout-exit-code" in first_args
    assert "125" in first_args
    assert "retrying" not in result.stderr


def test_detached_watch_preserves_explicit_hook_bin(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    hook_calls = tmp_path / "hook-calls.jsonl"
    fake_hook = tmp_path / "fake-hook"
    fake_hook.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${{1:-}}" == "hook" && "${{2:-}}" == "send" && "${{3:-}}" == "--help" ]]; then
  echo "custom help without codex phrase"
  exit 0
fi

python3 - <<'PY' "{str(hook_calls)}" "$@"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
args = sys.argv[2:]
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"args": args}}) + "\\n")
PY
""",
        encoding="utf-8",
    )
    fake_hook.chmod(fake_hook.stat().st_mode | stat.S_IXUSR)

    log_file = tmp_path / "watch.log"
    result = subprocess.run(
        [
            "bash",
            str(watch_script),
            "--session-key",
            "slack::channel::C123",
            "--hook-bin",
            str(fake_hook),
            "--log-file",
            str(log_file),
            "--",
            "bash",
            "-lc",
            "echo waiter output",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )

    assert result.returncode == 0, result.stderr
    deadline = time.time() + 5
    while time.time() < deadline:
        if hook_calls.exists():
            break
        time.sleep(0.05)

    assert hook_calls.exists(), log_file.read_text(encoding="utf-8")
    payload = json.loads(hook_calls.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["args"][:2] == ["hook", "send"]


def test_watch_then_hook_treats_timeout_zero_point_zero_as_no_timeout(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    hook_calls = tmp_path / "hook-calls.jsonl"
    fake_hook = tmp_path / "fake-hook"
    fake_hook.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${{1:-}}" == "hook" && "${{2:-}}" == "send" && "${{3:-}}" == "--help" ]]; then
  echo "--session-key"
  echo "Queue one asynchronous turn"
  exit 0
fi

python3 - <<'PY' "{str(hook_calls)}" "$@"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
args = sys.argv[2:]
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"args": args}}) + "\\n")
PY
""",
        encoding="utf-8",
    )
    fake_hook.chmod(fake_hook.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [
            "bash",
            str(watch_script),
            "--foreground",
            "--session-key",
            "slack::channel::C123",
            "--hook-bin",
            str(fake_hook),
            "--timeout",
            "0.0",
            "--",
            "bash",
            "-lc",
            "sleep 0.05; echo waiter output",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(hook_calls.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["args"][:2] == ["hook", "send"]


def test_forever_watch_accepts_lifetime_timeout_zero_point_zero_without_forever_error(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_github_pr_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_github_pr_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    _make_executable(
        script_dir / "watch_then_hook.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
    )
    _make_executable(
        script_dir / "wait_for_github_pr_activity.py",
        "#!/usr/bin/env python3\nprint('done')\n",
    )

    result = subprocess.run(
        [
            "bash",
            str(watch_script),
            "--foreground",
            "--session-key",
            "slack::channel::C123",
            "--repo",
            "cyhhao/vibe-remote",
            "--pr",
            "153",
            "--lifetime-timeout",
            "0.0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )

    assert result.returncode == 0, result.stderr
    assert "--lifetime-timeout requires --forever" not in result.stderr


def test_forever_watch_exits_on_non_retryable_cycle_failure(tmp_path: Path) -> None:
    source_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "background-watch-hook"
        / "scripts"
        / "watch_github_pr_then_hook.sh"
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    watch_script = script_dir / "watch_github_pr_then_hook.sh"
    shutil.copy2(source_script, watch_script)
    watch_script.write_text(
        watch_script.read_text(encoding="utf-8").replace("retry_delay_seconds=30", "retry_delay_seconds=0.1"),
        encoding="utf-8",
    )
    watch_script.chmod(watch_script.stat().st_mode | stat.S_IXUSR)

    _make_executable(
        script_dir / "watch_then_hook.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 2\n",
    )
    _make_executable(
        script_dir / "wait_for_github_pr_activity.py",
        "#!/usr/bin/env python3\nraise SystemExit('should not be executed directly in this test')\n",
    )

    result = subprocess.run(
        [
            "bash",
            str(watch_script),
            "--foreground",
            "--forever",
            "--session-key",
            "slack::channel::C123",
            "--repo",
            "cyhhao/vibe-remote",
            "--pr",
            "153",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )

    assert result.returncode == 2
    assert "non-retryable" in result.stderr
    assert "retrying" not in result.stderr
