#!/bin/bash
# Run E2E tests against a Docker container.
#
# Usage:
#   ./scripts/run_e2e.sh          # build + test + teardown
#   ./scripts/run_e2e.sh --keep   # keep container running after tests (for debugging)
#   ./scripts/run_e2e.sh --down   # just teardown

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.e2e.yml"

export VIBE_E2E_PORT="${VIBE_E2E_PORT:-15123}"

KEEP=false
DOWN_ONLY=false
PYTEST_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=true ;;
        --down) DOWN_ONLY=true ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

cleanup() {
    if [ "$KEEP" = false ]; then
        echo "Tearing down containers..."
        docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
    else
        echo "Keeping containers running (use --down to stop)"
    fi
}

if [ "$DOWN_ONLY" = true ]; then
    docker compose -f "$COMPOSE_FILE" down -v
    exit 0
fi

# Export KEEP flag so pytest fixture can respect it
export VIBE_E2E_KEEP="$KEEP"

# Build
echo "Building Docker image..."
docker compose -f "$COMPOSE_FILE" build

# Start
echo "Starting container (port $VIBE_E2E_PORT)..."
docker compose -f "$COMPOSE_FILE" up -d

# Wait for health
echo "Waiting for container to be healthy..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$VIBE_E2E_PORT/health" > /dev/null 2>&1; then
        echo "Container is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: Container did not become healthy in 30s"
        docker compose -f "$COMPOSE_FILE" logs
        cleanup
        exit 1
    fi
    sleep 1
done

# Run tests
echo "Running E2E tests..."
trap cleanup EXIT

set +e
pytest tests/e2e/ -v --tb=short ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
TEST_EXIT=$?
set -e

exit "$TEST_EXIT"
