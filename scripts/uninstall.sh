#!/usr/bin/env bash
# Full Muninn cleanup: LaunchAgents, venv, generated configs, logs, state.
# Vaults are NOT deleted — they're your data.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
_ORIG_DIR="$PWD"
cd "$PROJECT_ROOT"
trap 'cd "$_ORIG_DIR"' EXIT

echo "==> Muninn uninstall"
echo ""

# ---- 1. Unload LaunchAgents ----
LA_DIR="$HOME/Library/LaunchAgents"
for label in com.muninn.ingest com.muninn.consolidate com.muninn.maintain; do
  plist="$LA_DIR/$label.plist"
  if [[ -f "$plist" ]]; then
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "  removed LaunchAgent: $label"
  fi
done

# ---- 2. Kill running serve process ----
if pgrep -f "muninn serve" >/dev/null 2>&1; then
  pkill -f "muninn serve" 2>/dev/null || true
  echo "  stopped muninn serve"
fi

# ---- 3. Remove venv + any dangling symlinks in PATH ----
if [[ -d "$PROJECT_ROOT/.venv" ]]; then
  # Remove symlinks pointing into this venv (e.g. /usr/local/bin/muninn)
  for bindir in /usr/local/bin "$HOME/.local/bin" "$HOME/bin"; do
    link="$bindir/muninn"
    if [[ -L "$link" ]] && [[ "$(readlink "$link")" == *"$PROJECT_ROOT"* ]]; then
      if rm -f "$link" 2>/dev/null; then
        echo "  removed symlink $link"
      else
        sudo rm -f "$link" && echo "  removed symlink $link (sudo)" || echo "  [!] could not remove $link — run: sudo rm $link"
      fi
    fi
  done
  rm -rf "$PROJECT_ROOT/.venv"
  echo "  removed .venv/"
fi

# ---- 4. Remove generated .obsidian from demo vault ----
if [[ -d "$PROJECT_ROOT/Muninn-Demo/.obsidian" ]]; then
  rm -rf "$PROJECT_ROOT/Muninn-Demo/.obsidian"
  echo "  removed Muninn-Demo/.obsidian/"
fi

# ---- 5. Remove logs ----
LOG_DIR="$HOME/Library/Logs/Muninn"
if [[ -d "$LOG_DIR" ]]; then
  rm -rf "$LOG_DIR"
  echo "  removed $LOG_DIR"
fi

# ---- 6. Remove Python cache ----
find "$PROJECT_ROOT" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -rf "$PROJECT_ROOT"/*.egg-info 2>/dev/null || true
echo "  cleaned build artifacts"

echo ""
echo "Done. Your vaults, wiki.yaml, and .env are untouched."
echo ""
echo "  To also remove vault state (manifest, vectors):"
echo "    rm -rf <your-vault>/.muninn"
echo ""
echo "  To remove the repo entirely:"
echo "    cd .. && rm -rf Muninn"
