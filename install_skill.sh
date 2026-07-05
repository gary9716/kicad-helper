#!/bin/bash
set -euo pipefail
# Repo is single source of truth — installs via symlink. Re-run safe/idempotent.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TODAY="$(date +%Y%m%d)"

install_skills() {
  local TARGET_BASE="$1"
  for SKILL_DIR in "$SCRIPT_DIR/skills/"*/; do
    SKILL_NAME="$(basename "$SKILL_DIR")"
    DEST="$TARGET_BASE/$SKILL_NAME"
    if [ -L "$DEST" ]; then
      rm "$DEST"
    elif [ -e "$DEST" ]; then
      echo "Moving existing dir $DEST -> ${DEST}.bak-${TODAY}"
      mv "$DEST" "${DEST}.bak-${TODAY}"
    fi
    ln -s "$SKILL_DIR" "$DEST"
    echo "Linked $SKILL_NAME -> $DEST"
  done
}

mkdir -p "$HOME/.claude/skills"
install_skills "$HOME/.claude/skills"

if [ -d "$HOME/.gemini" ]; then
  mkdir -p "$HOME/.gemini/config/skills"
  install_skills "$HOME/.gemini/config/skills"
fi

echo "Done. Check loaded skills with the /skills command."
