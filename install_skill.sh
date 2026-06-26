#!/bin/bash
set -e

# Resolve the absolute path of this script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install every skill under skills/ into the Claude Code and Gemini skill dirs.
# Plain copy (not symlink) to match how kicad-helper was already installed.
for TARGET_BASE in "$HOME/.claude/skills" "$HOME/.gemini/config/skills"; do
  for SKILL_DIR in "$SCRIPT_DIR/skills/"*/; do
    SKILL_NAME="$(basename "$SKILL_DIR")"
    DEST="$TARGET_BASE/$SKILL_NAME"
    echo "Installing $SKILL_NAME -> $DEST"
    mkdir -p "$DEST"
    cp -R "$SKILL_DIR"* "$DEST"
  done
done

echo "Done. Check loaded skills with the /skills command."
