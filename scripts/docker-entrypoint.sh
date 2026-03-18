#!/bin/bash
set -e

cleanup() {
    python -c "
from vibe import runtime
runtime.stop_service()
runtime.stop_ui()
runtime.write_status('stopped', 'container shutdown')
"
}

trap cleanup EXIT INT TERM

start_ui_process() {
    local runtime_dir="$1"
    local ui_stdout="$runtime_dir/ui_stdout.log"
    local ui_stderr="$runtime_dir/ui_stderr.log"

    echo "Starting UI server on 0.0.0.0:${VIBE_UI_PORT:-5123}..." >&2
    python -c "
from vibe.ui_server import run_ui_server
run_ui_server('0.0.0.0', ${VIBE_UI_PORT:-5123})
" >>"$ui_stdout" 2>>"$ui_stderr" &

    local ui_pid=$!
    echo "$ui_pid" > "$runtime_dir/vibe-ui.pid"
    echo "$ui_pid"
}

# Ensure runtime directories exist and seed default config if missing
python -c "
from config.paths import ensure_data_dirs, get_config_path
ensure_data_dirs()
config_path = get_config_path()
if not config_path.exists():
    from vibe.runtime import default_config
    default_config().save(config_path)
"

MODE="${1:-ui}"

case "$MODE" in
    ui)
        # Start only the UI server (foreground) - for API E2E testing
        echo "Starting UI server on 0.0.0.0:${VIBE_UI_PORT:-5123}..."
        exec python -c "
from vibe.ui_server import run_ui_server
run_ui_server('0.0.0.0', ${VIBE_UI_PORT:-5123})
"
        ;;
    full)
        # Start service + UI server under a lightweight supervisor loop
        echo "Starting service..."
        python main.py &
        SERVICE_PID=$!

        # Write PID for runtime tracking
        RUNTIME_DIR="${VIBE_REMOTE_HOME:-$HOME/.vibe_remote}/runtime"
        mkdir -p "$RUNTIME_DIR"
        echo "$SERVICE_PID" > "$RUNTIME_DIR/vibe.pid"

        UI_PID="$(start_ui_process "$RUNTIME_DIR")"

        python -c "
from vibe import runtime
runtime.write_status('running', 'started', ${SERVICE_PID}, ${UI_PID})
"

        while true; do
            CURRENT_UI_PID=""
            if [ -f "$RUNTIME_DIR/vibe-ui.pid" ]; then
                CURRENT_UI_PID="$(cat "$RUNTIME_DIR/vibe-ui.pid")"
            fi

            if [ -z "$CURRENT_UI_PID" ]; then
                UI_PID="$(start_ui_process "$RUNTIME_DIR")"
                python -c "
from vibe import runtime
runtime.write_status('running', 'ui restarted', ${SERVICE_PID}, ${UI_PID})
"
            elif ! kill -0 "$CURRENT_UI_PID" 2>/dev/null; then
                wait "$CURRENT_UI_PID" 2>/dev/null || UI_EXIT_CODE=$?
                echo "UI server exited unexpectedly (code: ${UI_EXIT_CODE:-unknown}), restarting..."
                UI_PID="$(start_ui_process "$RUNTIME_DIR")"
                python -c "
from vibe import runtime
runtime.write_status('running', 'ui restarted after crash', ${SERVICE_PID}, ${UI_PID})
"
            fi

            sleep 1
        done
        ;;
    cli)
        # Run a vibe CLI command (e.g., docker run ... cli doctor)
        shift
        exec vibe "$@"
        ;;
    exec)
        # Run arbitrary command (for debugging)
        shift
        exec "$@"
        ;;
    *)
        echo "Usage: docker-entrypoint.sh {ui|full|cli|exec}"
        echo "  ui   - Start UI server only (default)"
        echo "  full - Start service + UI server"
        echo "  cli  - Run vibe CLI command"
        echo "  exec - Run arbitrary command"
        exit 1
        ;;
esac
