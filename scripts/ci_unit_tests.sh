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
# Excludes ``tests/e2e`` (Docker) and the ``integration`` marker (Docker +
# platform tokens) — those run in dedicated jobs. ``-p no:randomly`` keeps a
# deterministic order if pytest-randomly happens to be installed (no-op when it
# is not), and ``-o addopts=""`` ignores any ambient addopts.
set -uo pipefail

PYTEST=(python -m pytest)

failed=""
empty=""
total=0
while IFS= read -r f; do
  total=$((total + 1))
  "${PYTEST[@]}" "$f" -m "not integration" -p no:randomly -o addopts="" -q
  rc=$?
  if [ "$rc" -eq 0 ]; then
    :
  elif [ "$rc" -eq 5 ]; then
    # exit 5 = no tests collected (e.g. a file that is entirely integration-marked)
    empty="$empty $f"
  else
    failed="$failed $f"
  fi
done < <(find tests -name 'test_*.py' -not -path 'tests/e2e/*' | sort)

echo "========================================"
echo "Ran ${total} unit test file(s), one process each."
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
