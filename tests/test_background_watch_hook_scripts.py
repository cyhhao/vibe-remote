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
