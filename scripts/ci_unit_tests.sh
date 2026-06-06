#!/usr/bin/env bash
#
# Gate the unit test suite by running each test file in its OWN process.
#
# Why per-file: several test modules install module-level ``sys.modules`` stubs
# for optional platform deps (slack_sdk, aiohttp, modules.agents.*, core.*) that
# never tear down. Collecting the whole ``tests/`` tree in a single process
# therefore fails at import time as those stubs shadow real packages for later
# modules. Running each file in its own interpreter sidesteps that cross-file
# pollution. Making the stubs fixture-scoped so a single ``pytest tests/`` works
# is tracked separately; until then this is how the unit suite is CI-gated.
#
# CI may pass a shard index and total shard count to split the file list across
# multiple runners while preserving the one-process-per-file isolation. Shards
# are assigned by a deterministic file-size/test-count heuristic instead of by
# sorted-list modulo so large test modules are spread more evenly.
#
# Excludes ``tests/e2e`` (Docker) and the ``integration`` marker (Docker +
# platform tokens) — those run in dedicated jobs. ``-p no:randomly`` keeps a
# deterministic order if pytest-randomly happens to be installed (no-op when it
# is not), and ``-o addopts=""`` ignores any ambient addopts.
set -uo pipefail

SHARD_INDEX="${1:-0}"
SHARD_TOTAL="${2:-1}"

case "$SHARD_INDEX" in
  ''|*[!0-9]*)
    echo "Shard index must be a non-negative integer, got: $SHARD_INDEX" >&2
    exit 2
    ;;
esac

case "$SHARD_TOTAL" in
  ''|*[!0-9]*)
    echo "Shard total must be a positive integer, got: $SHARD_TOTAL" >&2
    exit 2
    ;;
esac

if [ "$SHARD_TOTAL" -lt 1 ]; then
  echo "Shard total must be at least 1, got: $SHARD_TOTAL" >&2
  exit 2
fi

if [ "$SHARD_INDEX" -ge "$SHARD_TOTAL" ]; then
  echo "Shard index must be less than shard total, got: $SHARD_INDEX >= $SHARD_TOTAL" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Unable to find python3 or python on PATH." >&2
    exit 127
  fi
fi

PYTEST=("$PYTHON_BIN" -m pytest)

select_unit_test_files() {
  "$PYTHON_BIN" - "$SHARD_INDEX" "$SHARD_TOTAL" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

shard_index = int(sys.argv[1])
shard_total = int(sys.argv[2])

files = sorted(
    path
    for path in Path("tests").rglob("test_*.py")
    if "tests/e2e/" not in path.as_posix()
)


def estimate_weight(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    line_count = text.count("\n") + 1
    test_count = len(re.findall(r"^\s*(?:async\s+def|def)\s+test_", text, re.MULTILINE))
    class_count = len(re.findall(r"^\s*class\s+Test", text, re.MULTILINE))
    return max(1, line_count + (test_count * 20) + (class_count * 60))


weighted_files = [(estimate_weight(path), path.as_posix()) for path in files]
shards: list[dict[str, object]] = [
    {"weight": 0, "files": []}
    for _ in range(shard_total)
]

for weight, file_path in sorted(weighted_files, key=lambda item: (-item[0], item[1])):
    target = min(
        range(shard_total),
        key=lambda index: (
            shards[index]["weight"],
            len(shards[index]["files"]),  # type: ignore[arg-type]
            index,
        ),
    )
    shards[target]["weight"] = int(shards[target]["weight"]) + weight
    shard_files = shards[target]["files"]
    assert isinstance(shard_files, list)
    shard_files.append(file_path)

selected_files = sorted(shards[shard_index]["files"])
print(
    "Planned "
    f"{len(files)} unit test file(s) across {shard_total} shard(s); "
    f"shard {shard_index} has {len(selected_files)} file(s), "
    f"estimated weight {shards[shard_index]['weight']}.",
    file=sys.stderr,
)
for file_path in selected_files:
    print(file_path)
PY
}

failed=""
empty=""
selected=0
discovered=0
while IFS= read -r f; do
  discovered=$((discovered + 1))
  selected=$((selected + 1))
  started_at=$(date +%s)
  "${PYTEST[@]}" "$f" -m "not integration" -p no:randomly -o addopts="" -q
  rc=$?
  finished_at=$(date +%s)
  elapsed=$((finished_at - started_at))
  echo "Finished $f in ${elapsed}s with exit code $rc."
  if [ "$rc" -eq 0 ]; then
    :
  elif [ "$rc" -eq 5 ]; then
    # exit 5 = no tests collected (e.g. a file that is entirely integration-marked)
    empty="$empty $f"
  else
    failed="$failed $f"
  fi
done < <(select_unit_test_files)

discovered=$(find tests -name 'test_*.py' -not -path 'tests/e2e/*' | sort | wc -l | tr -d ' ')

echo "========================================"
echo "Discovered ${discovered} unit test file(s)."
echo "Ran ${selected} unit test file(s), one process each, for shard ${SHARD_INDEX}/${SHARD_TOTAL}."
if [ -n "$empty" ]; then
  echo "No unit tests collected (skipped):"
  for f in $empty; do echo "  $f"; done
fi
if [ -n "$failed" ]; then
  echo "FAILED files:"
  for f in $failed; do echo "  $f"; done
  exit 1
fi
echo "All unit test files passed."
