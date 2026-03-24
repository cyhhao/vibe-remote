#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.three-regression.yml"
PROJECT_NAME="vibe-three-regression"
ENV_FILE="$REPO_ROOT/.env.three-regression"
OUTPUT_ROOT="$REPO_ROOT/_tmp/three-regression"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DOCKER_BIN="${DOCKER_BIN:-}"

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
    echo "Copy $REPO_ROOT/.env.three-regression.example to .env.three-regression first." >&2
    exit 1
fi

print_summary() {
    local port="${THREE_REGRESSION_PORT:-15130}"
    local ui_host="${THREE_REGRESSION_UI_HOST:-127.0.0.1}"
    local default_backend="${THREE_REGRESSION_DEFAULT_BACKEND:-opencode}"

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

    local shared_root="$OUTPUT_ROOT/shared-home"
    mkdir -p "$shared_root"

    echo "Snapshotting agent runtime state from existing regression container..."
    snapshot_container_path "/root/.claude" "$shared_root" ".claude"
    snapshot_container_path "/root/.claude.json" "$shared_root" ".claude.json"
    snapshot_container_path "/root/.codex" "$shared_root" ".codex"
    snapshot_container_path "/root/.config/opencode" "$shared_root/.config" "opencode"
    snapshot_container_path "/root/.local/share/opencode" "$shared_root/.local/share" "opencode"
}

wait_for_service() {
    local port="$1"
    local url="http://127.0.0.1:${port}"

    for _ in $(seq 1 60); do
        if curl -sf "$url/health" >/dev/null 2>&1 && \
            curl -sf "$url/status" | "$PYTHON_BIN" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("running") else 1)' >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done

    echo "Service did not become ready on port $port" >&2
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs vibe || true
    return 1
}

case "$MODE" in
    down)
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

snapshot_agent_runtime_state

echo "Stopping previous regression container..."
"$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true

echo "Preparing generated config and state..."
PREPARE_ARGS=(--output-root "$OUTPUT_ROOT")
PREPARE_ARGS+=(--reset-mode "$RESET_MODE")
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_three_regression.py" "${PREPARE_ARGS[@]}"

echo "Starting unified regression container..."
if [ -n "$BUILD_FLAG" ]; then
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --force-recreate --remove-orphans
else
    "$DOCKER_BIN" compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
fi

echo "Waiting for service to become healthy..."
wait_for_service "${THREE_REGRESSION_PORT:-15130}"

print_summary
