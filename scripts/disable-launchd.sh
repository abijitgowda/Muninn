#!/usr/bin/env bash
# Unload LaunchAgents and remove the plists. Leaves the vault + config in place.
set -euo pipefail

LA_DIR="$HOME/Library/LaunchAgents"

for label in com.muninn.ingest com.muninn.consolidate com.muninn.maintain; do
  plist="$LA_DIR/$label.plist"
  if [[ -f "$plist" ]]; then
    echo "==> launchctl unload $plist"
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "    removed $plist"
  else
    echo "    not installed: $plist"
  fi
done

echo "==> Done. LaunchAgents disabled."
echo "    Full cleanup: ./scripts/uninstall.sh"
