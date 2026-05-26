---
name: muninn-wiki
description: The architecture reference for the Muninn wiki. Read first when working in this vault. Explains the three-layer model, page kinds, folder routing, and key invariants.
---

# muninn-wiki

## 3-layer model

1. **Raw** — `Raw/Sources/<connector>/<date>/` and `Raw/Files/`. Immutable. Read only.
2. **Wiki** — `Wiki/`. LLM-owned. Interconnected markdown with `[[wikilinks]]`.
3. **Schema** — `Schema/` + `AGENTS.md` + `.agents/skills/`. The rules.

## 3 operations

| Operation | Skill | Cadence |
|---|---|---|
| Ingest | muninn-ingest | On new raw source (hourly via launchd) |
| Query | muninn-query | On demand |
| Lint / Maintain | muninn-lint, muninn-maintain | Weekly (Sundays via launchd) |

## Key invariants

- Sources are immutable. Never edit `Raw/`.
- Every claim cites a source via `sources:` frontmatter + inline `^[extracted/inferred/ambiguous]` markers.
- Non-stub pages have at least 2 outgoing `[[wikilinks]]`.
- `Wiki/index.md` stays current -- it is the cheapest retrieval pass.

## Page kinds

`concept` | `person` | `org` | `ticker` | `topic` | `project` | `source-summary` | `doc` | `redirect`

Required fields per kind: [Schema/frontmatter-schema.md](../../Schema/frontmatter-schema.md)

## Folder routing

See [Schema/naming-conventions.md](../../Schema/naming-conventions.md).

## When stuck

- Naming: naming-conventions
- Frontmatter: frontmatter-schema
- Valid page criteria: lint-checklist
- End-to-end flow: workflow-examples
- None of the above: ask the human.
