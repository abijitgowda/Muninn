# Muninn Demo Vault

This is the **demo Obsidian vault** for Muninn — a local, self-maintaining knowledge base that builds a digital brain from your digital footprint.

> "Instead of asking an LLM the same questions over and over (RAG every time), you compile knowledge once into interconnected markdown files and keep them current. Obsidian is the viewer. The LLM is the maintainer."

## What this demo contains

A single source — Andrej Karpathy's LLM Wiki gist — has been ingested through the pipeline, producing 9 interlinked wiki pages across Concepts, Entities, Topics, and Projects. This shows the structure and conventions that a real vault follows at scale.

## The three layers

| Folder | Layer | Who writes here | Edit by hand? |
|---|---|---|---|
| `Raw/` | **Sources** — immutable inputs | Connectors (browser history, X, URLs) and you (drop files in `Raw/Files/`) | Only `Raw/Files/`. Never `Raw/Sources/`. |
| `Wiki/` | **Knowledge** — distilled pages | The LLM, via the `muninn-ingest` skill | Rarely — let the LLM maintain it. Manual edits are fine but mark with `lifecycle: pinned`. |
| `Schema/` + `AGENTS.md` + `.agents/skills/` | **Rules** — how the wiki is shaped | You | Yes. This is where you teach the LLM your conventions. |

## Folder map

```
Muninn-Demo/
├── Raw/
│   ├── Files/             # Drop PDFs, .md notes here yourself
│   └── Sources/url/       # Connector outputs
├── Schema/                # Rules: frontmatter, naming, lint checklist
├── Wiki/
│   ├── index.md           # Master catalog (LLM-maintained)
│   ├── overview.md        # Auto-generated summary
│   ├── Sources/           # Source summaries
│   ├── Concepts/          # Theories, methods, ideas
│   ├── Entities/          #   People/, Orgs/, Tickers/
│   ├── Topics/            # Cross-cutting themes
│   └── Projects/          # Anything you're actively working on
├── Tutorial/              # 4-step walkthrough
├── Scripts/               # Human-readable runbooks
├── .agents/skills/        # LLM wiki skills (ingest, query, lint, ...)
└── AGENTS.md              # Read-this-first for any LLM
```

## Open this vault in Obsidian

`File -> Open vault -> Open folder as vault` -> pick `Muninn-Demo/`. The graph view will show the interconnected demo pages.

## Skills

The `.agents/skills/` folder ships with:

- `muninn-wiki` — architecture reference (read first)
- `muninn-ingest` — extract, resolve, merge
- `muninn-query` — answer questions with citations
- `muninn-lint` — orphans, broken links, stale claims
- `muninn-maintain` — weekly upkeep
- `muninn-cross-linker` — find unlinked mentions
- `muninn-ingest-url`, `muninn-ingest-x`, `muninn-ingest-browser-history` — connector-specific guidance

See `Welcome.md` for first-time orientation and `Tutorial/` for a 4-step walkthrough.
