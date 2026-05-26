#!/usr/bin/env bash
# Driver for the maintain LaunchAgent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') maintain start ====="
"$PROJECT_ROOT/.venv/bin/muninn" lint || echo "lint reported issues (non-fatal)"
exec "$PROJECT_ROOT/.venv/bin/muninn" maintain
