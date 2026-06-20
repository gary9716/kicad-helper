#!/bin/bash
set -e

# Resolve the absolute path of this script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Target global skills directory
TARGET_DIR="$HOME/.gemini/config/skills/kicad-helper"

echo "Installing kicad-helper skill to global config..."
mkdir -p "$TARGET_DIR"
cp -R "$SCRIPT_DIR/skills/kicad-helper/"* "$TARGET_DIR"

echo "Successfully installed kicad-helper skill!"
echo "You can check loaded skills inside agy TUI using the /skills command."
