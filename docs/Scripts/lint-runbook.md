---
title: "Runbook — Lint"
kind: doc
tags: [runbook]
lifecycle: pinned
---

# Runbook — Lint

When the wiki starts to drift (broken links, orphans, stale claims), run lint.

## Report only

```bash
muninn lint
```

Prints a categorized report. Exit code 0 = clean, nonzero = issues found. Useful in CI.

```bash
muninn lint --json > lint-report.json
```

For programmatic consumers.

## Auto-fix (consolidate mode)

```bash
muninn lint --consolidate
```

For each fixable category, the tool prints the proposed change and waits for `y/n`. Categories it can fix:

- **Index drift** — regenerates `Wiki/index.md` sections
- **Cross-linking** — inserts `[[wikilinks]]` for unlinked mentions
- **Frontmatter normalization** — adds missing required fields with sensible defaults
- **Tag aliases** — normalizes `LLM/llm/Llm/LLMs` etc.

Things it **won't** auto-fix:

- Broken wikilinks (pick a rename target manually)
- Contradictions (human judgment)
- Provenance drift (re-ingest with more sources)

## What the categories mean

See [[Schema/lint-checklist]] for the full list. Brief reminder:

| Category | Sample finding |
|---|---|
| Frontmatter | "Wiki/Concepts/RLHF.md missing `confidence:`" |
| Broken wikilinks | "Wiki/Topics/AI Safety.md → [[Constitutional AI]] (no such page)" |
| Orphans | "Wiki/Concepts/Persistent Knowledge Artifact.md has zero incoming links" |
| Stale | "Wiki/Concepts/LLM Wiki.md updated=2026-04-01 but newest source read=2026-05-15" |
| Provenance | "Wiki/Concepts/AGI Timeline.md inferred=0.8 (>0.5 threshold)" |
| Index drift | "Wiki/Concepts/Memex.md exists but is not listed in Wiki/index.md" |

## Weekly maintain pass

The `maintain` op = lint + cross-linker + index regen + overview regen. Runs Sundays 03:00 via launchd. To trigger manually:

```bash
muninn maintain
```

## When you disagree with a lint finding

The system has false positives. If a finding is wrong:

- For a single page: add `lint_ignore: [check-name]` to its frontmatter.
- Globally: add a rule to `Schema/lint-checklist.md` and re-run lint.

## Rebuild from scratch (nuclear option)

If the wiki has drifted beyond repair:

```bash
# Re-queue and re-ingest from Raw/Sources/ — wiki stays live
muninn reindex
```

The archive lives in `Muninn-Vault/.muninn/archives/<timestamp>/`. Nothing is lost; you can `--restore <timestamp>` later.
