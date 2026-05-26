---
title: "03 — Querying your wiki"
kind: doc
tags: [tutorial]
lifecycle: pinned
---

# 03 — Querying your wiki

You've ingested some sources (see [[tutorial/02-first-ingest]]). Now ask the wiki questions.

## From the terminal

```bash
muninn query "what have I been reading about LLM wikis?"
```

The pipeline:

1. Loads the `muninn-query` skill.
2. Reads `Wiki/index.md` (cheapest pass).
3. If the index doesn't have enough, greps frontmatter `summary:` fields.
4. If still not enough, opens full bodies of top candidates.
5. Synthesizes an answer with `[[wikilink]]` citations.

## From Obsidian (Copilot)

### 1. Start the serve endpoint

```bash
muninn serve --port 19828
```

This runs an OpenAI-compatible API at `http://127.0.0.1:19828`. Keep it running in a terminal tab.

### 2. Install Copilot for Obsidian

1. Open Obsidian → **Settings** → **Community plugins** → **Browse**
2. Search for **Copilot** (`logancyang/obsidian-copilot`)
3. Install and enable it

### 3. Configure Copilot to use Muninn

1. **Settings** → **Copilot** → **Model providers** → **Add custom OpenAI-compatible provider**
   - **Base URL:** `http://127.0.0.1:19828/v1`
   - **API key:" anything (e.g. `muninn``) — or your `MUNINN_API_TOKEN` if set
   - **Model name:" `muninn``
2. Set **Default Model** to `muninn`
3. Optional: in **QA** settings, set the **Embedding Model** to Ollama's `mxbai-embed-large` at `http://localhost:11434` for local vault search

### 4. Start chatting

Open the Copilot chat panel (`Cmd+P` → "Copilot: Open Copilot Chat") and ask questions. Responses include clickable `[[wikilinks]]` to your wiki pages.

The pipeline behind the scenes: keyword + vector retrieval → spreading activation → LLM synthesis with citations.

## From Claude Code

If you have Claude Code installed and the vault is open:

```
$ claude
> /muninn-query "what do I know about RLHF?"
```

The skill is auto-discovered from `.claude/skills/` (symlinked to `.agents/skills/`).

## Query modes

```bash
muninn query "..."              # default — tiered retrieval
muninn query "..." --quick      # index-only (fast, less complete)
muninn query "..." --deep       # reads 12+ candidate bodies (thorough)
muninn query "..." --cite-only  # just list which pages would be cited
muninn query "..." --json       # structured output
muninn query --explain "[[LLM Wiki]]"  # explain a specific page
```

## Free-form exploration

You're encouraged to **just navigate**. The graph view (`Cmd+G`) is excellent for serendipity. Click any page, follow its wikilinks, see what your past self wrote.

## When the answer feels wrong

The query result includes confidence indicators based on each cited page's `lifecycle:` field. If you see `[draft]` next to a citation, the page is unreviewed.

To improve:

1. Open the cited page in Obsidian.
2. Edit it directly — set `lifecycle: pinned` to lock against future LLM rewrites.
3. Re-run the query.

Next: [[tutorial/04-add-a-source]].
