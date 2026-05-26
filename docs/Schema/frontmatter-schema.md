---
title: Frontmatter Schema
kind: doc
tags: [meta/schema]
lifecycle: pinned
---

# Frontmatter schema

Every wiki page **must** open with YAML frontmatter delimited by `---`. The exact required fields depend on the page `kind`.

## Universal fields (all pages)

```yaml
---
title: "Reinforcement Learning from Human Feedback"
kind: concept              # one of: concept | person | org | topic | project | log-day | source-summary | doc
summary: "Aligning LLMs to human preferences via a reward model trained on comparisons."  # ≤200 chars
tags: [ai/training, alignment]
sources:                   # list of source-page references in Wiki/sources/
  - "[[2026-05-22-browser-history-example-source]]"
created: 2026-05-22
updated: 2026-05-22
confidence: medium         # low | medium | high
lifecycle: draft           # draft | stable | stale | pinned | superseded
provenance:                # rough mix of claim origins on this page
  extracted: 0.7
  inferred: 0.2
  ambiguous: 0.1
---
```

## Per-kind extras

### `kind: concept`

```yaml
related_concepts: ["[[Reinforcement Learning]]", "[[Direct Preference Optimization]]"]
field: ai/ml               # broad academic / professional field
```

### `kind: person`

```yaml
roles: ["researcher", "founder"]
affiliations: ["[[OpenAI]]", "[[Tesla]]"]
notable_for: "Co-founded OpenAI, Director of AI at Tesla, deep-learning educator"
url: "https://example.com"
```

### `kind: org`

```yaml
org_type: "company"        # company | university | lab | govt | community | other
founded: 2015
url: "https://openai.com"
people: ["[[Jensen Huang]]"]
```

### `kind: topic`

```yaml
spans: ["[[Concept A]]", "[[Concept B]]"]   # the concepts this topic ties together
```

### `kind: project`

```yaml
status: active             # active | paused | shipped | abandoned
started: 2026-05-01
owners: ["[[You]]"]
goal: "One-sentence outcome"
```

### `kind: log-day`

The filename is `YYYY-MM-DD.md`. Required fields:

```yaml
title: "2026-05-22"
kind: log-day
date: 2026-05-22
sources_ingested: 7
pages_touched: 23
```

### `kind: source-summary`

These live in `Wiki/sources/` and are auto-generated during ingest:

```yaml
source_type: browser-history     # which connector
source_ref: "https://example.com/article"
read_date: 2026-05-22
ingested_at: 2026-05-22T14:30:00
pages_touched: ["[[Concept A]]", "[[Person B]]"]
raw: "[[Raw/Sources/chrome-history/2026-05-22/abc123.md|raw]]"
```

## Required vs optional

- **Required for every kind**: `title`, `kind`, `summary`, `tags`, `created`, `updated`, `lifecycle`.
- **Required where applicable**: `sources` (any page making factual claims), `confidence` (any page beyond a stub).
- **`provenance` block**: required once a page has more than ~5 claims.

## Validation

`muninn lint` runs the checks in [[Schema/lint-checklist]]. The CI-style report flags every page missing required fields.
