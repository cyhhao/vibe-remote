#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.three-regression.yml"
PROJECT_NAME="vibe-three-regression"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DOCKER_BIN="${DOCKER_BIN:-}"
CONTAINER_HOME="/home/avibe"
CONTAINER_AVIBE_HOME="$CONTAINER_HOME/.avibe"

resolve_git_common_repo_root() {
    local common_dir
    common_dir="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -z "$common_dir" ]; then
        echo "$REPO_ROOT"
        return 0
    fi
    case "$common_dir" in
        /*) ;;
        *) common_dir="$REPO_ROOT/$common_dir" ;;
    esac
    (cd "$common_dir/.." && pwd)
}

absolute_from_repo_root() {
    local path="$1"
    case "$path" in
        /*) echo "$path" ;;
        *) echo "$REPO_ROOT/$path" ;;
    esac
}

CANONICAL_REPO_ROOT="$(resolve_git_common_repo_root)"
DEFAULT_OUTPUT_ROOT="$CANONICAL_REPO_ROOT/.runtime/three-regression"
OUTPUT_ROOT="${THREE_REGRESSION_STATE_ROOT:-$DEFAULT_OUTPUT_ROOT}"
if [ -f "$REPO_ROOT/.env.three-regression" ]; then
    ENV_FILE="$REPO_ROOT/.env.three-regression"
else
    ENV_FILE="$CANONICAL_REPO_ROOT/.env.three-regression"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

resolve_docker_bin() {
    if [ -n "$DOCKER_BIN" ] && [ -x "$DOCKER_BIN" ]; then
        return 0
    fi

    if command -v docker >/dev/null 2>&1; then
        DOCKER_BIN="$(command -v docker)"
        return 0
    fi

    for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
        if [ -x "$candidate" ]; then
            DOCKER_BIN="$candidate"
            export PATH="$(dirname "$candidate"):$PATH"
            return 0
        fi
    done

    echo "Docker CLI not found in PATH or common install locations." >&2
    echo "Set DOCKER_BIN=/absolute/path/to/docker if Docker is installed elsewhere." >&2
    exit 1
}

resolve_docker_bin

MODE="up"
BUILD_FLAG="--build"
RESET_MODE="none"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/run_three_regression.sh
  ./scripts/run_three_regression.sh --no-build
  ./scripts/run_three_regression.sh --reset-config
  ./scripts/run_three_regression.sh --reset-all
  ./scripts/run_three_regression.sh --down
  ./scripts/run_three_regression.sh --status
  ./scripts/run_three_regression.sh --logs
  ./scripts/run_three_regression.sh --env-file /path/to/.env.three-regression

Environment:
  THREE_REGRESSION_STATE_ROOT       Override the persistent state root.
  THREE_REGRESSION_SHOW_RUNTIME_SOURCE
                                    Show Runtime provider for regression.
                                    Defaults to github-source.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)
            if [ $# -lt 2 ]; then
                echo "--env-file requires a path argument" >&2
                usage >&2
                exit 1
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --down)
            MODE="down"
            shift
            ;;
        --status)
            MODE="status"
            shift
            ;;
        --logs)
            MODE="logs"
            shift
            ;;
        --no-build)
            BUILD_FLAG=""
            shift
            ;;
        --reset-config)
            RESET_MODE="config"
            shift
            ;;
        --reset-all)
            RESET_MODE="all"
            shift
            ;;
        --reset-state)
            RESET_MODE="all"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

load_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        return 1
    fi

    set -a
    . "$ENV_FILE"
    set +a
    return 0
}

ENV_LOADED=false
if load_env_file; then
    ENV_LOADED=true
elif [ "$MODE" = "up" ]; then
    echo "Missing env file: $ENV_FILE" >&2
    echo "Copy .env.three-regression.example to .env.three-regression in either this worktree or the primary checkout first." >&2
    exit 1
fi

OUTPUT_ROOT="${THREE_REGRESSION_STATE_ROOT:-$DEFAULT_OUTPUT_ROOT}"
OUTPUT_ROOT="$(absolute_from_repo_root "$OUTPUT_ROOT")"
export THREE_REGRESSION_STATE_ROOT="$OUTPUT_ROOT"
export THREE_REGRESSION_SHOW_RUNTIME_SOURCE="${THREE_REGRESSION_SHOW_RUNTIME_SOURCE:-github-source}"
export THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REPO="${THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REPO:-https://github.com/avibe-bot/vibe-show-runtime.git}"
export THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REF="${THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REF:-main}"

LOCK_DIR=""
LOCK_HELD=false

release_run_lock() {
    if [ "$LOCK_HELD" != true ] || [ -z "$LOCK_DIR" ] || [ ! -d "$LOCK_DIR" ]; then
        return 0
    fi

    local lock_pid
    lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ "$lock_pid" = "$$" ]; then
        rm -rf "$LOCK_DIR"
    fi
}

acquire_run_lock() {
    mkdir -p "$OUTPUT_ROOT"
    LOCK_DIR="$OUTPUT_ROOT/.run.lock"

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        LOCK_HELD=true
    else
        local existing_pid=""
        existing_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
        if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
            echo "Another three-regression update is already running." >&2
            echo "Lock: $LOCK_DIR" >&2
            echo "PID: $existing_pid" >&2
            echo "Repo: $(cat "$LOCK_DIR/repo_root" 2>/dev/null || echo unknown)" >&2
            exit 1
        fi

        echo "Removing stale three-regression lock: $LOCK_DIR" >&2
        rm -rf "$LOCK_DIR"
        if ! mkdir "$LOCK_DIR" 2>/dev/null; then
            echo "Failed to acquire three-regression lock: $LOCK_DIR" >&2
            exit 1
        fi
        LOCK_HELD=true
    fi

    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    printf '%s\n' "$REPO_ROOT" > "$LOCK_DIR/repo_root"
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LOCK_DIR/started_at"
    trap 'release_run_lock' EXIT
    trap 'release_run_lock; exit 130' INT
    trap 'release_run_lock; exit 143' TERM
}

if [ "$MODE" = "up" ] || [ "$MODE" = "down" ]; then
    acquire_run_lock
fi

print_summary() {
    local port="${THREE_REGRESSION_PORT:-15130}"
    local bind_host="${THREE_REGRESSION_PORT_BIND_HOST:-127.0.0.1}"
    local ui_host="${THREE_REGRESSION_ACCESS_HOST:-${THREE_REGRESSION_UI_HOST:-$bind_host}}"
    local default_backend="${THREE_REGRESSION_DEFAULT_BACKEND:-opencode}"
    local config_path="$OUTPUT_ROOT/home/.avibe/config/config.json"
    local display_root
    display_root="$OUTPUT_ROOT"
    case "$display_root" in
        "$CANONICAL_REPO_ROOT"/*) display_root="${display_root#"$CANONICAL_REPO_ROOT/"}" ;;
    esac

    if [ -f "$config_path" ]; then
        default_backend="$("$PYTHON_BIN" - "$config_path" "$default_backend" <<'PY'
import json
import sys

path, fallback = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    print((payload.get("agents") or {}).get("default_backend") or fallback)
except Exception:
    print(fallback)
PY
)"
    fi

    local slack_channel="${THREE_REGRESSION_SLACK_CHANNEL:-}"
    local discord_channel="${THREE_REGRESSION_DISCORD_CHANNEL:-}"
    local feishu_channel="${THREE_REGRESSION_FEISHU_CHAT_ID:-}"
    local wechat_channel="${THREE_REGRESSION_WECHAT_CHANNEL:-}"

    [ -z "$slack_channel" ] && slack_channel="(configure later in UI)"
    [ -z "$discord_channel" ] && discord_channel="(configure later in UI)"
    [ -z "$feishu_channel" ] && feishu_channel="(configure later in UI)"
    [ -z "$wechat_channel" ] && wechat_channel="(QR login required in UI)"

    cat <<EOF
Unified regression environment is ready:
  URL: http://${ui_host}:${port}
  Default backend: ${default_backend}
  State root: ${display_root}
  Show Runtime source: ${THREE_REGRESSION_SHOW_RUNTIME_SOURCE}

  Platform routing:
  - Slack:   channel=${slack_channel}  backend=${THREE_REGRESSION_SLACK_BACKEND:-${default_backend}}
  - Discord: channel=${discord_channel}  backend=${THREE_REGRESSION_DISCORD_BACKEND:-${default_backend}}
  - Feishu:  channel=${feishu_channel}  backend=${THREE_REGRESSION_FEISHU_BACKEND:-${default_backend}}
  - WeChat:  channel=${wechat_channel}  backend=${THREE_REGRESSION_WECHAT_BACKEND:-${default_backend}}
EOF
}

container_exists() {
    local cid
    cid="$("$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps -q vibe 2>/dev/null || true)"
    [ -n "$cid" ]
}

snapshot_container_path() {
    local container_path="$1"
    local host_parent="$2"
    local host_name="$3"

    if ! "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe sh -lc "test -e '$container_path'" >/dev/null 2>&1; then
        return 0
    fi

    mkdir -p "$host_parent"
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe sh -lc \
        "tar --ignore-failed-read --warning=no-file-changed -C \"$(dirname "$container_path")\" -cf - \"$(basename "$container_path")\"" \
        | tar -C "$host_parent" -xf - || true
}

snapshot_agent_runtime_state() {
    if [ "$RESET_MODE" = "all" ]; then
        return 0
    fi

    if ! container_exists; then
        return 0
    fi

    local home_root="$OUTPUT_ROOT/home"
    mkdir -p "$home_root"

    echo "Snapshotting agent runtime state from existing regression container..."
    snapshot_container_path "$CONTAINER_HOME/.claude" "$home_root" ".claude"
    snapshot_container_path "$CONTAINER_HOME/.claude.json" "$home_root" ".claude.json"
    snapshot_container_path "$CONTAINER_HOME/.codex" "$home_root" ".codex"
    snapshot_container_path "$CONTAINER_HOME/.config/opencode" "$home_root/.config" "opencode"
    snapshot_container_path "$CONTAINER_HOME/.local/share/opencode" "$home_root/.local/share" "opencode"
}

snapshot_avibe_state() {
    if [ "$RESET_MODE" = "all" ]; then
        return 0
    fi

    if ! container_exists; then
        return 0
    fi

    local cid
    cid="$("$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps -q vibe 2>/dev/null || true)"
    if [ -z "$cid" ]; then
        return 0
    fi

    local avibe_target="$OUTPUT_ROOT/home/.avibe"
    if [ -f "$avibe_target/config/config.json" ]; then
        return 0
    fi

    mkdir -p "$OUTPUT_ROOT/home"
    echo "Importing Avibe state from the existing regression container..."
    "$DOCKER_BIN" cp "$cid:$CONTAINER_AVIBE_HOME/." "$avibe_target" >/dev/null 2>&1 || true
}

write_regression_metadata() {
    mkdir -p "$OUTPUT_ROOT"
    "$PYTHON_BIN" - "$OUTPUT_ROOT" "$REPO_ROOT" "$CANONICAL_REPO_ROOT" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

output_root = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
canonical_repo_root = Path(sys.argv[3])

def git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None

payload = {
    "schema_version": 1,
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "state_root": str(output_root),
    "repo_root": str(repo_root),
    "canonical_repo_root": str(canonical_repo_root),
    "branch": git_value("branch", "--show-current"),
    "commit": git_value("rev-parse", "HEAD"),
}
(output_root / "metadata.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

prepare_show_runtime() {
    echo "Preparing Show Runtime inside the regression container..."
    if ! "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe vibe runtime prepare --strict; then
        echo "Show Runtime preparation failed inside the regression container." >&2
        "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe vibe runtime status --json >&2 || true
        return 1
    fi

    local status_json
    status_json="$("$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe vibe runtime status --json)"
    STATUS_JSON="$status_json" "$PYTHON_BIN" - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_JSON"])
if not payload.get("command"):
    reason = payload.get("reason") or "runtime_command_missing"
    raise SystemExit(f"Show Runtime is not executable after prepare: {reason}")
print(
    "Show Runtime ready: "
    f"provider={payload.get('provider')} "
    f"platform={payload.get('platform')} "
    f"installed={payload.get('installed')}"
)
PY
}

wait_for_service() {
    for _ in $(seq 1 60); do
        if "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" exec -T vibe python - <<'PY' >/dev/null 2>&1; then
import json
import sys
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:5123/health", timeout=3).read()
    status = json.loads(urllib.request.urlopen("http://127.0.0.1:5123/status", timeout=3).read())
except Exception:
    sys.exit(1)
sys.exit(0 if status.get("running") else 1)
PY
            return 0
        fi
        sleep 2
    done

    echo "Service did not become ready inside the regression container" >&2
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs vibe || true
    return 1
}

case "$MODE" in
    down)
        snapshot_avibe_state
        snapshot_agent_runtime_state
        "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans
        exit 0
        ;;
    status)
        "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps
        if [ "$ENV_LOADED" = true ]; then
            print_summary
        fi
        exit 0
        ;;
    logs)
        "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs -f vibe
        exit 0
        ;;
esac

snapshot_avibe_state
snapshot_agent_runtime_state

echo "Stopping previous regression container..."
"$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true

echo "Preparing generated config and state..."
PREPARE_ARGS=(--output-root "$OUTPUT_ROOT")
PREPARE_ARGS+=(--reset-mode "$RESET_MODE")
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_three_regression.py" "${PREPARE_ARGS[@]}"
write_regression_metadata

echo "Starting unified regression container..."
if [ -n "$BUILD_FLAG" ]; then
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --force-recreate --remove-orphans
else
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
fi

echo "Waiting for service to become healthy..."
wait_for_service "${THREE_REGRESSION_PORT:-15130}"
prepare_show_runtime

print_summary
