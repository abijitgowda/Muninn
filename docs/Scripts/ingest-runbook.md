---
title: "Runbook — Ingest"
kind: doc
tags: [runbook]
lifecycle: pinned
---

# Runbook — Ingest

When something goes sideways with ingestion, follow this.

## Manual ingest commands

```bash
# All enabled sources (manifest tracks state):
muninn ingest --all

# One source, with output cap:
muninn ingest --source chrome-history --limit 20

# One URL:
muninn ingest-url https://example.com/article

# Dry run (extract & log but don't write to Wiki/):
muninn ingest --all --dry-run
```

## Inspecting state

```bash
muninn status
# Prints:
#   sources:
#     chrome-history    cursor=2026-05-22T13:00:00  pending=0  processed=47
#     edge-history      cursor=2026-05-22T13:00:00  pending=0  processed=12
#     abi-x             cursor=2026-05-22T08:00:00  pending=3  processed=89
```

Inspect raw items directly:

```bash
ls -la Muninn-Vault/Raw/Sources/chrome-history/
```

Inspect manifest:

```bash
cat Muninn-Vault/.muninn/manifest.json | jq .
```

## Resetting a single item

A raw item failed to process? Re-queue it:

```bash
# Find the item ID in the manifest, then:
muninn ingest --reprocess <item-id>
```

Or edit `manifest.json` and set its `status: pending`.

## Resetting a source cursor

To re-ingest a source from scratch:

```bash
muninn sources reset chrome-history
# confirms then sets cursor=1970-01-01 and clears processed history
```

Use with care — this re-creates many raw files and re-runs the LLM on each.

## When the LLM merge produces garbage

Most common cause: the page got duplicate sections because merge picked a different heading style.

1. Open the offending page in Obsidian.
2. Manually clean it.
3. Set `lifecycle: pinned` in the frontmatter to lock against future automated rewrites.

## When the scheduler isn't running

```bash
launchctl list | grep muninn
# Should show 2 entries:
# com.muninn.ingest
# com.muninn.maintain

# Logs:
tail -f ~/Library/Logs/Muninn/ingest.log
tail -f ~/Library/Logs/Muninn/ingest.err
```

Reload an agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.muninn.ingest.plist
launchctl load   ~/Library/LaunchAgents/com.muninn.ingest.plist
```

## When Ollama isn't responding

```bash
ollama list                # confirm model is pulled
ollama ps                  # what's currently loaded
curl http://localhost:11434/api/tags   # raw API ping
```

If Ollama is down, ingest will write raw files but the LLM step will fail and items remain `pending`. They'll be re-tried on the next scheduler run.
