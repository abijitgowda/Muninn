---
name: muninn-ingest
description: Distill a raw source into wiki pages. Extract entities, concepts, topics, and claims, merge into existing pages or create new ones, log the operation, and update the index. Use when given a path to a raw file under Raw/Sources/.
---

# muninn-ingest

Given a raw source at `Raw/Sources/<connector>/<date>/<id>.md`, compile its knowledge into the wiki.

## The 6-step protocol

**1. Read** the raw file's frontmatter (`source_type`, `source_url`, `read_date`, `title`) and body.

**2. Extract** structured knowledge -- produce: `summary` (<=200 chars), `entities` (name + kind + context), `concepts`, `topics`, `claims` (statement + confidence), `open_questions`, `key_quotes`. Drop noise, keep signal.

**3. Resolve** each item against existing pages: exact slug -> alias -> fuzzy. Exists -> merge. Missing -> create.

**4. Write pages.**
- Create: correct folder per naming-conventions, full frontmatter per frontmatter-schema.
- Merge: preserve existing wikilinks, append source to `sources:`, update `updated:`, expand `provenance:`.
- Mark provenance inline: `^[extracted]` (verbatim from source), `^[inferred]` (your synthesis), `^[ambiguous]` (sources disagree).

**5. Write source-summary** at `Wiki/sources/<YYYY-MM-DD>-<connector>-<slug>.md` with `pages_touched:`, takeaways, quotes, open questions, and `raw:` link.

**6. Update log + index.**
- Append to `.logs/log-<YYYY-MM>.md`: `## [HH:MM] ingest | <source-name> | <summary>` with Touched/Raw/Source links.
- Add new pages to `Wiki/index.md` between `<!-- BEGIN: x -->` / `<!-- END: x -->` markers.

## Quality checks

- Touched 5-20 pages (fewer = under-extracting, more = over-eager).
- Every new page has required frontmatter and >= 2 outgoing `[[wikilinks]]`.
- Source-summary `pages_touched` is accurate. Log has an entry. Index lists every new page.

## Edge cases

- **Empty/paywalled source:** minimal source-summary noting "no body extractable."
- **Duplicate:** manifest shows already processed -> abort.
- **Contradiction:** add to page's Contradictions section with `^[ambiguous]`. Never silently overwrite.
- **Can't classify kind:** default to `concept`.

## Connector-specific skills

muninn-ingest-browser-history, muninn-ingest-x, muninn-ingest-url, muninn-ingest-market.
