#!/bin/bash

# Restart script for vibe-remote bot
# This script simply calls start.sh which handles killing existing instances

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Restarting vibe-remote bot..."
exec "$SCRIPT_DIR/start.sh"
