---
title: "Runbook — Query"
kind: doc
tags: [runbook]
lifecycle: pinned
---

# Runbook — Query

## Asking from terminal

```bash
muninn query "..."
# or
echo "..." | muninn query --stdin
```

## Asking from Claude Code

Inside the vault (with skills symlinked into `.claude/skills/`):

```
$ claude
> /llm-wiki-query "..."
```

## Asking from Obsidian

If you have the Obsidian "Smart Connections" or "BMO" plugin, point it at the local Ollama endpoint (`http://localhost:11434`) and the same model. Then ask freely — the plugin doesn't follow the tiered retrieval protocol, but it will see the same pages.

## Modes

```bash
--quick       # index-only retrieval (fast, less complete)
--deep        # opens every plausibly-related body (slow, thorough)
--cite-only   # returns just the cited pages, no synthesis
--json        # structured output
```

## Hot-path: explain a single page

```bash
muninn query --explain "[[LLM Wiki]]"
# Synthesizes the page using its sources, frontmatter, and inbound links.
```

## When the answer feels off

1. Re-run with `--deep` — the index may have been outdated.
2. Check `Wiki/index.md` opening — if it's stale, run `muninn maintain`.
3. Open the cited pages — see if they actually say what the answer claims.
4. If a cited page is wrong, edit it and pin it (`lifecycle: pinned`) so it stops getting overwritten.

## Performance

- `--quick`: ~3–8 seconds with qwen2.5:14b on M-series.
- Default tiered: ~15–40 seconds.
- `--deep`: ~1–3 minutes (reads many pages into context).

## Privacy

All queries stay local. No network calls. The `query.log` (under `~/Library/Logs/Muninn/`) records query text and which pages were read, useful for debugging but contains your questions — delete it periodically if you care.
