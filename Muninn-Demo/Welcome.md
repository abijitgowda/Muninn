---
title: Welcome
kind: doc
tags: [meta/welcome]
created: "2026-01-15"
updated: "2026-01-15"
lifecycle: pinned
---
# Welcome to Muninn

> *Muninn* — your local memory. Private by design. Like the hippocampus consolidating memories during sleep, Muninn continuously transforms raw experience into lasting knowledge.

## What's here

This vault contains your wiki — knowledge pages automatically generated from your digital footprint. The structure:

- **Wiki/** — your knowledge graph. Concepts, people, organizations, topics, projects — all interlinked with `[[wikilinks]]`.
- **Raw/Sources/** — immutable snapshots of what you consumed (browser pages, articles, documents).
- **Inbox/** — drop files here for automatic ingestion.

## Getting started

1. Browse the [[Wiki/index|index]] to see all pages at a glance.
2. Open the graph view (`Cmd+G`) to see how pages connect.
3. Ask the wiki a question via Copilot chat (`Cmd+P` → "Copilot: Open Copilot Chat").

## Quick commands

```bash
muninn ingest-url https://example.com/article   # ingest a URL
muninn ingest --all                               # ingest all sources
muninn query "what have I been reading about?"    # ask from terminal
muninn serve                                      # start Copilot bridge
```

## How it works

Sources flow in one direction: **Raw → LLM → Wiki**. Each source item gets one LLM call for extraction. Page creation, merging, and cross-linking are deterministic Python — no LLM variability in the write path.

Pages have `strength` that decays over time, `access_count` that grows when cited in queries, and nightly consolidation that rewrites pages for clarity.
