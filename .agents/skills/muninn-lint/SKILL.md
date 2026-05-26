---
name: muninn-lint
description: Run health checks on the wiki -- orphans, broken wikilinks, missing frontmatter, stale claims, contradictions, provenance drift, and index drift. Use to audit vault quality. With --consolidate, propose and apply safe auto-fixes.
---

# muninn-lint

Run checks defined in [Schema/lint-checklist.md](../../Schema/lint-checklist.md). Produce a categorized report.

## The 8 check categories

1. **Frontmatter** -- validate required fields per kind against [frontmatter-schema](../../Schema/frontmatter-schema.md). Severity: ERROR.
2. **Broken wikilinks** -- every `[[Target]]` resolves to an existing page. Severity: WARN.
3. **Orphans** -- pages with zero incoming links. Severity: WARN.
4. **Stale pages** -- `updated:` older than 60 days, `lifecycle:` not `pinned`. Severity: INFO.
5. **Provenance drift** -- `inferred > 0.5` or `ambiguous > 0.3` in a page's provenance block. Severity: WARN.
6. **Index drift** -- pages exist in Wiki/ but are missing from `Wiki/index.md`, or vice versa. Severity: ERROR.
7. **Low connectivity** -- non-stub pages with fewer than 2 outgoing wikilinks. Severity: INFO.
8. **Contradictions** -- claims marked `^[ambiguous]` that conflict across pages (LLM-based check). Severity: WARN.

## Severity levels

- **ERROR** -- must fix before other operations.
- **WARN** -- should fix.
- **INFO** -- consider fixing.

## --consolidate mode

Safe auto-fixes: index drift (regenerate sections), cross-linking (delegate to muninn-cross-linker), missing frontmatter (sensible defaults), tag alias normalization, log gaps.

Leave to humans: broken-wikilink renames, contradictions, provenance drift, page merges.

## Output example

```
[OK] Frontmatter -- all required fields present
[WARN] Broken wikilinks (3):
  - Wiki/Concepts/RLHF.md -> [[Constitutional AI]] (no such page)
[ERROR] Index drift (1):
  - Wiki/Concepts/Memex.md exists but not in Wiki/index.md

Summary: 1 error, 3 warnings across 247 pages.
```
