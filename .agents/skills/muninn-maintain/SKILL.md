---
name: muninn-maintain
description: Weekly vault maintenance -- lint, cross-link, regenerate index, rebuild overview, flag stale areas. Run after batch ingests or on Sundays via launchd.
---

# muninn-maintain

Weekly housekeeping to re-knit the vault and surface decay.

## The 5-step sweep

1. **Lint** -- run muninn-lint. ERROR findings stop the sweep.
2. **Cross-link** -- run muninn-cross-linker to insert `[[wikilinks]]` for unlinked mentions.
3. **Regenerate index** -- rewrite each section in `Wiki/index.md` between `<!-- BEGIN: x -->` / `<!-- END: x -->` markers. Sort by `updated:` desc, one bullet per page.
4. **Rebuild overview** -- write `Wiki/overview.md` with: top 10 hubs (by incoming links), clusters (BFS on wikilink graph), recent additions (last 7 days), stale areas (`updated:` > 60 days, not pinned), and harvested `open_questions:`.
5. **Log** -- append to `Wiki/log.md` and `Wiki/Logs/<today>.md`: lint findings count, cross-links inserted, index sections regenerated, stale pages flagged.

## Rules

- Use `kind: redirect` + `superseded_by:` instead of deleting pages.
- Leave a redirect stub when renaming.
- Surface contradictions for humans; never auto-resolve them.
- Skip pages with `lifecycle: pinned`.
- The sweep is idempotent: a second run produces no diff.
