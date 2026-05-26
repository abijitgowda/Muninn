# AGENTS.md — read this first

You are operating inside the **Muninn demo vault**, an LLM-maintained knowledge base. Your job is to read raw sources, distill them into interconnected wiki pages, and keep the whole thing coherent over time.

This file is the contract. Everything else in `Schema/` and `.agents/skills/` elaborates on it.

---

## Cardinal rules

1. **Never edit `Raw/Sources/`.** That folder is owned by the ingestion connectors. You read from it, you don't write to it. `Raw/Files/` (where the human drops PDFs/notes) is also read-only to you.
2. **You own `Wiki/`.** Create, update, merge pages here. Always preserve existing `[[wikilinks]]` when merging.
3. **`Schema/` and `AGENTS.md` are scripture.** Follow the rules. If a rule seems wrong, surface it — don't silently break it.
4. **Every claim gets a source.** Use the `sources:` frontmatter list. Use `^[inferred]` for synthesis, `^[ambiguous]` when sources disagree.
5. **Wikilinks are the connective tissue.** A page with zero `[[links]]` is a smell. Aim for 2+ outgoing links on any non-stub page.

---

## The three operations

### Ingest (`/muninn-ingest` or the headless ingest pipeline)

When given a raw source (a tweet, a URL, a PDF page, a browser-history visit):

1. **Read** the raw file in `Raw/Sources/<source>/<date>/<id>.md`.
2. **Extract** entities (people, orgs, products), concepts (ideas, methods, theories), topics (cross-cutting themes), claims, open questions. Drop noise; keep signal.
3. **Resolve** — for each extracted item, search `Wiki/` for an existing page:
   - If found: merge (preserve old wikilinks, add new info, update `updated:` date, expand `sources:`).
   - If not found: create a new page with proper frontmatter.
4. **Cross-link** — add `[[wikilinks]]` between the new/updated pages.
5. **Log** — append a one-line entry to `.logs/` with the prefix `## [YYYY-MM-DD HH:MM] ingest | <source-name> | <one-line summary>`.
6. **Index** — if you created new pages, add them to `Wiki/index.md` under the right category with a 1-line description.

A single source should touch **10-15 pages** on average. If you only touched 1-2, you're probably under-extracting. If you touched 30+, you're being too eager.

### Query (`/muninn-query`)

Use **tiered retrieval**:

1. Read `Wiki/index.md` first — it has a 1-line summary per page.
2. If that's enough, answer. Cite with `[[wikilinks]]`.
3. If not, grep page frontmatter (`summary:` field).
4. If still not enough, read full bodies of the top candidates.
5. Never make up `[[links]]` — only cite pages that exist.

### Lint (`/muninn-lint`)

Check (see `Schema/lint-checklist.md` for full list):

- Orphans (pages with zero incoming links)
- Broken `[[wikilinks]]` (target doesn't exist)
- Missing required frontmatter fields
- Stale pages (`updated:` older than newest `sources[].read_date`)
- Contradictions across pages
- Index/file mismatch

Report, suggest fixes, only auto-apply when explicitly told `--consolidate`.

---

## Page kinds and where they live

| Kind | Folder | Example |
|---|---|---|
| `concept` | `Wiki/Concepts/` | "LLM Wiki" |
| `person` | `Wiki/Entities/People/` | "Andrej Karpathy" |
| `org` | `Wiki/Entities/Orgs/` | "Anthropic" |
| `ticker` | `Wiki/Entities/Tickers/` | "NVDA" |
| `topic` | `Wiki/Topics/` | "Personal Knowledge Management" |
| `project` | `Wiki/Projects/` | "Muninn" |
| `source-summary` | `Wiki/Sources/` | "2026-01-15-url-karpathy-llm-wiki" |

Use proper frontmatter from `Schema/frontmatter-schema.md` for every new page.

---

## Provenance markers (place inline after the claim)

- `^[extracted]` — pulled directly from a source (default; can be omitted)
- `^[inferred]` — your synthesis from one or more sources
- `^[ambiguous]` — sources disagree; show both sides

---

## When in doubt

- Read `Schema/frontmatter-schema.md` for what each page must contain.
- Read `Schema/naming-conventions.md` for slug/folder rules.
- Read `Schema/workflow-examples.md` for a worked end-to-end example.
- Read `.agents/skills/muninn-wiki/SKILL.md` for the architecture reference.

If a user instruction conflicts with this file, **ask them to clarify** before acting. Don't silently override the rules.
