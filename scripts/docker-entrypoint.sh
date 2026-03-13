#!/bin/bash
set -e

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
        # Start service in background + UI server in foreground
        echo "Starting service..."
        python main.py &
        SERVICE_PID=$!

        # Write PID for runtime tracking
        RUNTIME_DIR="${VIBE_REMOTE_HOME:-$HOME/.vibe_remote}/runtime"
        mkdir -p "$RUNTIME_DIR"
        echo "$SERVICE_PID" > "$RUNTIME_DIR/vibe.pid"

        echo "Starting UI server on 0.0.0.0:${VIBE_UI_PORT:-5123}..."
        python -c "
from vibe.ui_server import run_ui_server
run_ui_server('0.0.0.0', ${VIBE_UI_PORT:-5123})
" &
        UI_PID=$!
        echo "$UI_PID" > "$RUNTIME_DIR/vibe-ui.pid"

        # Wait for either process to exit; propagate its exit code
        set +e
        wait -n "$SERVICE_PID" "$UI_PID" 2>/dev/null
        EXIT_CODE=$?
        set -e
        # Terminate the sibling process
        kill "$SERVICE_PID" "$UI_PID" 2>/dev/null || true
        wait "$SERVICE_PID" "$UI_PID" 2>/dev/null || true
        exit "$EXIT_CODE"
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
