---
title: Lint Checklist
kind: doc
tags: [meta/schema, meta/lint]
lifecycle: pinned
---

# Lint checklist

The checks `muninn lint` (and the `/llm-wiki-lint` skill) run, in order. Each check has a **detection** rule and a **suggested fix**.

## 1. Missing frontmatter

**Detect:** page has no opening `---` block, or is missing any required field from [[Schema/frontmatter-schema]] for its `kind:`.
**Fix:** add the missing fields. For `kind:` choose the best fit from filename/path.

## 2. Broken wikilinks

**Detect:** `[[Target]]` where no file `Target.md` exists in the vault (across all subfolders).
**Fix:**
- Fuzzy-match (case-insensitive, plural/singular, punctuation) against existing pages → suggest rename.
- If no match → either create a stub page (when the link target is a real entity) or delete the link.

## 3. Orphan pages

**Detect:** page has zero **incoming** wikilinks.
**Fix:** ask `cross-linker` to find unlinked mentions across the vault. If still orphaned, the page is probably misfiled — flag for human review.

## 4. Missing outgoing links

**Detect:** non-stub page (>200 words) with fewer than 2 outgoing `[[wikilinks]]`.
**Fix:** scan the body for capitalized noun phrases that match existing page titles and convert them.

## 5. Stale claims

**Detect:** page `updated:` timestamp older than the newest `read_date:` of any source on `sources[]`.
**Fix:** re-merge the affected source, or add a `staleness:` callout:

```markdown
> [!warning] Stale since 2026-04-01
> Source [[2026-04-01-...]] may contradict claims here.
```

## 6. Contradictions

**Detect:** sentences with similar embeddings on different pages that contain opposing keywords (`true ↔ false`, `increases ↔ decreases`, `present ↔ absent`).
**Fix:** raise both claims to the `Contradictions` section of the broader topic page and mark with `^[ambiguous]`. Don't silently pick a winner.

## 7. Index drift

**Detect:** `Wiki/index.md` lists a page that doesn't exist, or omits a page that does.
**Fix:** regenerate the relevant index section.

## 8. Provenance ratio

**Detect:** page `provenance:` shows `inferred > 0.5` OR `ambiguous > 0.3`.
**Fix:** flag for human review — the page is mostly speculation.

## 9. Fragmented tag clusters

**Detect:** group of pages sharing a tag has internal-linking cohesion < 0.15 (links-within-cluster / pages-in-cluster²).
**Fix:** ask `cross-linker` to weave them; if still fragmented, the tag may be too broad — suggest splitting.

## 10. Confidence / lifecycle mismatch

**Detect:** `lifecycle: stable` with `confidence: low`, or `lifecycle: pinned` with `provenance.extracted < 0.5`.
**Fix:** demote lifecycle, or upgrade confidence by adding more sources.

## 11. Misfiled pages

**Detect:** `kind:` doesn't match the folder (e.g., a `kind: concept` page inside `Wiki/Entities/`).
**Fix:** move the file. Update incoming links.

## 12. Daily log gaps

**Detect:** `Wiki/Logs/` is missing a `YYYY-MM-DD.md` for a day where the manifest shows sources were ingested.
**Fix:** generate a synthetic log from the manifest.

## 13. Source coverage

**Detect:** raw item in `Raw/Sources/<x>/.../<id>.md` has no `Wiki/sources/...` summary linking back.
**Fix:** queue the raw item for ingestion (set `status: pending` in manifest).

## Modes

```bash
muninn lint               # report only
muninn lint --consolidate # apply fixes interactively
muninn lint --json        # machine-readable
```
