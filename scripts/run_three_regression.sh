#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

exec "$SCRIPT_DIR/run_regression.sh" "$@"
