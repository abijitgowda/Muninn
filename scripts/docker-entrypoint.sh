#!/usr/bin/env bash
# Docker entrypoint: starts cron scheduler + muninn serve.
set -euo pipefail

# Install crontab if present
if [[ -f /app/crontab ]]; then
  crontab /app/crontab
  cron
  echo "==> Scheduler started (ingest hourly, consolidate 2am, maintain Sun 3am)"
fi

# Run whatever command was passed (default: muninn serve)
exec muninn "$@"
