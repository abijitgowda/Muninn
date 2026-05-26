---
name: muninn-query
description: Answer a user's question by reading the wiki and citing sources with [[wikilinks]]. Use when the user asks a knowledge question or wants to look something up in the vault.
---

# muninn-query

Answer using only pages that exist in the wiki. Start with `Wiki/index.md` and read deeper as needed.

## Output format

```
<Synthesized answer in 1-3 paragraphs. Mention pages inline: "According to [[Page Name]], ...">

Sources:
- [[Page A]] -- <how it contributed>
- [[Page B]] -- <how it contributed>

Confidence: high | medium | low
```

## Confidence calibration

Base confidence on the `lifecycle:` field of cited pages:
- `pinned` or `stable` -> high
- `draft` -> medium (say so: "based on a single draft page")
- `stale` or `superseded` -> low (warn the user)

## Source-summary pages

Source-summaries (`Wiki/sources/`) are useful for tracing where knowledge came from. Cite them when provenance matters.

## When the wiki has nothing

Say so honestly: "The wiki has nothing on `<topic>` yet." Suggest ingesting a relevant URL. Never fabricate answers.
