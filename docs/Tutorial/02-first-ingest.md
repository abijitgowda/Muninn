---
title: "02 — Your first ingest"
kind: doc
tags: [tutorial]
lifecycle: pinned
---

# 02 — Your first ingest

You have the tooling set up (see [[tutorial/01-getting-started]]). Now let's ingest something.

## Option A — A single URL (fastest)

```bash
muninn ingest-url https://example.com/article-you-want-to-ingest
```

What happens:

1. The `url` connector fetches the page and extracts clean markdown (defuddle-style).
2. The raw markdown lands in `Raw/Sources/url/2026-MM-DD/<slug>.md`.
3. The `muninn-ingest` skill is fed the raw + the existing wiki state.
4. Ollama extracts entities, concepts, topics.
5. New pages get created under `Wiki/Concepts/`, `Wiki/Entities/`, etc.
6. `Wiki/index.md` is updated.

Then open Obsidian and switch to graph view (`Cmd+G`). You should see ~10 connected nodes.

## Option B — Browser history (real use case)

Enable edge or chrome history in `wiki.yaml` (enabled by default). Then:

```bash
muninn ingest --source edge-history --limit 10 -v
```

This pulls your last 10 distinct page visits (excluding domains in the exclude list), defuddles each, and runs the same ingest flow.

## Option C — All enabled sources

```bash
muninn ingest --all -v
```

Run as many times as you want — the manifest tracks what's already been processed, so subsequent runs only handle new items.

## Option D — Wait for the scheduler

After `muninn init`, the LaunchAgent runs hourly. Just keep browsing — within an hour, the wiki starts populating itself.

## Checking what's been ingested

```bash
muninn status
```

Shows per-source cursor and pending items.

## Inspect a single raw item

```
Raw/Sources/edge-history/2026-05-23/<id>.md
```

Open it in Obsidian. The frontmatter shows when it was visited, the URL, the title. The body is the defuddled content.

Then open the corresponding source-summary in `Wiki/sources/`. That's where the LLM recorded its takeaways.

## What if it goes wrong?

- Log: `~/Library/Logs/Muninn/ingest.log` (when run via launchd) or stdout (when run manually).
- Re-run with `-v` for verbose output: `muninn ingest --all -v`.
- Reset a single item: `muninn ingest --reprocess <item-id>`.
- Reset an entire source to re-ingest from scratch: `muninn sources reset edge-history`.

## Rebuilding the wiki

If pages are shallow or you changed the LLM model:

```bash
muninn reindex                # re-queue + re-ingest from Raw/Sources/, wiki stays live
muninn ingest --all -v        # re-processes from preserved Raw/Sources/
```

Next: [[tutorial/03-querying]].
