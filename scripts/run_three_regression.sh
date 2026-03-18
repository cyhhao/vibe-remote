#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.three-regression.yml"
PROJECT_NAME="vibe-three-regression"
ENV_FILE="$REPO_ROOT/.env.three-regression"
OUTPUT_ROOT="$REPO_ROOT/_tmp/three-regression"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

MODE="up"
BUILD_FLAG="--build"
LOGS_SERVICE=""
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
  ./scripts/run_three_regression.sh --logs [slack|discord|feishu]
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
            if [ $# -gt 1 ] && [[ "$2" != --* ]]; then
                LOGS_SERVICE="$2"
                shift 2
            else
                shift
            fi
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
    local slack_channel="${THREE_REGRESSION_SLACK_CHANNEL:-}"
    local discord_channel="${THREE_REGRESSION_DISCORD_CHANNEL:-}"
    local feishu_channel="${THREE_REGRESSION_FEISHU_CHAT_ID:-}"

    if [ -z "$slack_channel" ]; then
        slack_channel="(configure later in UI)"
    fi
    if [ -z "$discord_channel" ]; then
        discord_channel="(configure later in UI)"
    fi
    if [ -z "$feishu_channel" ]; then
        feishu_channel="(configure later in UI)"
    fi

    cat <<EOF
Three-end regression environment is ready:
- Slack:  http://127.0.0.1:${THREE_REGRESSION_SLACK_PORT:-15131}  channel=${slack_channel}  backend=${THREE_REGRESSION_SLACK_BACKEND}
- Discord: http://127.0.0.1:${THREE_REGRESSION_DISCORD_PORT:-15132}  channel=${discord_channel}  backend=${THREE_REGRESSION_DISCORD_BACKEND}
- Feishu: http://127.0.0.1:${THREE_REGRESSION_FEISHU_PORT:-15133}  channel=${feishu_channel}  backend=${THREE_REGRESSION_FEISHU_BACKEND}
EOF
}

wait_for_service() {
    local service="$1"
    local port="$2"
    local url="http://127.0.0.1:${port}"

    for _ in $(seq 1 60); do
        if curl -sf "$url/health" >/dev/null 2>&1 && \
            curl -sf "$url/status" | "$PYTHON_BIN" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("running") else 1)' >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done

    echo "Service $service did not become ready on port $port" >&2
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs "$service" || true
    return 1
}

case "$MODE" in
    down)
        docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans
        exit 0
        ;;
    status)
        docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps
        if [ "$ENV_LOADED" = true ]; then
            print_summary
        fi
        exit 0
        ;;
    logs)
        if [ -n "$LOGS_SERVICE" ]; then
            docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs -f "$LOGS_SERVICE"
        else
            docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs -f
        fi
        exit 0
        ;;
esac

echo "Stopping previous three-end regression containers..."
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true

echo "Preparing generated config and state..."
PREPARE_ARGS=(--output-root "$OUTPUT_ROOT")
PREPARE_ARGS+=(--reset-mode "$RESET_MODE")
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_three_regression.py" "${PREPARE_ARGS[@]}"

echo "Starting three-end regression containers..."
if [ -n "$BUILD_FLAG" ]; then
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --force-recreate --remove-orphans
else
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans
fi

echo "Waiting for services to become healthy..."
wait_for_service slack "${THREE_REGRESSION_SLACK_PORT:-15131}"
wait_for_service discord "${THREE_REGRESSION_DISCORD_PORT:-15132}"
wait_for_service feishu "${THREE_REGRESSION_FEISHU_PORT:-15133}"

print_summary
