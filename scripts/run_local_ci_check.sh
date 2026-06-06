#!/usr/bin/env bash
#
# Run the local equivalent of the CI unit-test gate without mutating the
# developer's global Python, uv tool installation, or live Vibe Remote state.
set -euo pipefail

SHARD_INDEX="${1:-0}"
SHARD_TOTAL="${2:-1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORK_ROOT="${TMPDIR:-/tmp}/vibe-remote-local-ci"
RUN_DIR="$(mktemp -d "${WORK_ROOT}.XXXXXX")"
VENV_DIR="$RUN_DIR/.venv"
STATE_DIR="$RUN_DIR/.vibe_remote"

cleanup() {
  if [ "${VIBE_KEEP_LOCAL_CI_TMP:-0}" = "1" ]; then
    echo "Keeping temporary local CI directory: $RUN_DIR"
    return
  fi
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

cd "$REPO_ROOT"

if [ -z "${PYTHON:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
  else
    echo "Unable to find python3 or python on PATH." >&2
    exit 127
  fi
fi

echo "Using temporary venv: $VENV_DIR"
"$PYTHON" -m venv "$VENV_DIR"
PYTHON_BIN="$VENV_DIR/bin/python"

echo "Using isolated VIBE_REMOTE_HOME: $STATE_DIR"
export VIBE_REMOTE_HOME="$STATE_DIR"
export PYTHON="$PYTHON_BIN"

if [ "${VIBE_LOCAL_CI_SKIP_UI_BUILD:-0}" != "1" ]; then
  echo "Building UI assets..."
  (cd ui && npm ci && npm run build)
elif [ ! -d ui/dist ]; then
  echo "VIBE_LOCAL_CI_SKIP_UI_BUILD=1 was set, but ui/dist does not exist." >&2
  echo "Unset VIBE_LOCAL_CI_SKIP_UI_BUILD or build ui/dist before running the local CI check." >&2
  exit 2
fi

echo "Installing editable package into the temporary venv..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -e . pytest

echo "Running CI unit-test shard ${SHARD_INDEX}/${SHARD_TOTAL}..."
bash scripts/ci_unit_tests.sh "$SHARD_INDEX" "$SHARD_TOTAL"
