---
title: Workflow Examples
kind: doc
tags: [meta/schema, meta/examples]
lifecycle: pinned
---

# Workflow examples

Walk-through of the three core operations with concrete file paths.

## Example 1 — Ingesting a single article

**Source:** you read https://example.com/ai-knowledge-architecture at 14:30. Chrome history connector picks it up the next hour.

**Step 1.** Connector writes raw file:

```
Raw/Sources/chrome-history/2026-05-22/llm-wiki-gist.md
```

with frontmatter:

```yaml
---
source_type: browser-history
source_url: https://example.com/ai-knowledge-architecture
visit_count: 3
read_date: 2026-05-22T14:30:00
title: "LLM Wiki — a knowledge architecture"
---
[full defuddled article body here]
```

**Step 2.** Manifest records `{source: chrome-history, item: <hash>, status: pending}`.

**Step 3.** `llm-wiki-ingest` skill picks it up. It extracts:

- **Entities:** `Jensen Huang`, `Tim Berners-Lee`, `OpenAI`, `Tesla`
- **Concepts:** `LLM Wiki`, `Memex`, `Retrieval-Augmented Generation`, `Persistent Knowledge Artifact`
- **Topics:** `personal knowledge management`, `AI-assisted research`
- **Claims:** "LLM Wiki compounds knowledge over time", "Single source touches 10-15 pages"
- **Questions:** "How does this scale past 1000 pages?"

**Step 4.** Pages touched (12 in this case):

| Page | Action |
|---|---|
| `Wiki/Concepts/LLM Wiki.md` | **Created** — main concept page |
| `Wiki/Concepts/Memex.md` | **Created** — historical antecedent |
| `Wiki/Concepts/Retrieval-Augmented Generation.md` | **Updated** — add "compared with LLM Wiki" |
| `Wiki/Concepts/Persistent Knowledge Artifact.md` | **Created** |
| `Wiki/Entities/People/Jensen Huang.md` | **Updated** — add "originator of LLM Wiki pattern (2026)" |
| `Wiki/Entities/People/Tim Berners-Lee.md` | **Created** |
| `Wiki/Entities/Orgs/OpenAI.md` | **Updated** — Karpathy co-founder ref |
| `Wiki/Entities/Orgs/Tesla.md` | **Updated** — Karpathy AI director ref |
| `Wiki/Topics/Personal Knowledge Management.md` | **Updated** — add LLM-Wiki section |
| `Wiki/Topics/AI-Assisted Research.md` | **Created** |
| `Wiki/sources/2026-05-22-browser-history-llm-wiki-gist.md` | **Created** — source summary |
| `Wiki/Logs/2026-05-22.md` | **Appended** — one-line log entry |
| `Wiki/index.md` | **Updated** — 5 new entries added |

**Step 5.** Cross-linker pass adds `[[LLM Wiki]]` and `[[Jensen Huang]]` references on related existing pages.

**Step 6.** Manifest marks item as `processed`.

---

## Example 2 — Querying

**You:** "What have I learned about LLM wikis vs RAG?"

**Tiered retrieval:**

1. Read `Wiki/index.md` → finds `LLM Wiki`, `Retrieval-Augmented Generation`, `Persistent Knowledge Artifact`.
2. Read frontmatter `summary:` of those three.
3. Open full bodies of the top 2.
4. Synthesize:

> Based on `[[LLM Wiki]]` and `[[Retrieval-Augmented Generation]]`, the key difference is that RAG rediscovers knowledge on every query, while LLM Wiki *compiles knowledge once* into interconnected markdown files that an LLM maintains over time. ^[extracted]
>
> The implications: LLM Wiki performs better at small-to-medium scale (~100 sources) where the bookkeeping is the bottleneck, RAG remains useful when sources are too numerous to compile. ^[inferred]
>
> See `[[Wiki/sources/2026-05-22-browser-history-llm-wiki-gist]]` for the original article.

Citations: only `[[wikilinks]]` to pages that actually exist.

---

## Example 3 — Weekly lint pass

Run `muninn maintain` on Sunday at 03:00 (via launchd).

```
Linting 247 pages...
✓ Frontmatter: 245/247 (2 missing `confidence`)
✗ Broken wikilinks: 3
  - Wiki/Concepts/RLHF.md → [[Constitutional AI]]  (no such page)
  - Wiki/Topics/AI Safety.md → [[Anthropic Founders]]  (no such page; close match: Anthropic)
  - Wiki/Entities/People/Karpathy.md  (alias, should be Jensen Huang)
✗ Orphans: 4
  - Wiki/Concepts/Persistent Knowledge Artifact.md (zero incoming)
  - ...
✓ Stale pages: 0
✗ Provenance drift: 1
  - Wiki/Concepts/AGI Timeline.md (inferred=0.8)
✗ Index drift: 1
  - Wiki/Concepts/Memex.md not listed in index
Cross-linker: 17 unlinked mentions inserted.
Index regenerated.
Overview.md regenerated.
```

The maintenance run only auto-fixes safe things (index/overview regen, cross-linking). Risky fixes (broken-link rename, page merge) await human approval next time you run `muninn lint --consolidate`.
