---
name: muninn-ingest-browser-history
description: Connector-specific guidance for ingesting browser-history raw items. Handles visit-count prioritization, URL special cases, and noise filtering. Use when processing raw files with source_type browser-history.
---

# muninn-ingest-browser-history

Raw item frontmatter includes `visit_count`, `last_visit`, `title`, `defuddled` (boolean), and `source_url`. Body is the defuddled article text (if available).

## Key fields for prioritization

- **visit_count >= 5**: signals strong interest. Create dedicated pages even from thin sources.
- **visit_count == 1 + body < 150 words**: skip (set manifest status to `skipped`).
- **defuddled: true**: body is clean markdown. Ingest normally.
- **defuddled: false** or body < 200 words: note "raw body partial; ingested from title + URL context only."

## URL special cases

- **YouTube** (`youtube.com/watch?v=`): no body available. Stub source-summary from title only. Create concept pages only if visit_count is high.
- **Google Docs / Notion**: auth-walled. Same treatment as YouTube.
- **GitHub**: READMEs extract well. PR/issue URLs ingest normally; tag resulting pages with `tag: github`.
- **HN / Reddit**: extract the article topic + most-upvoted claims. Mark discussion claims as `^[ambiguous]`.

## Source-summary extras

Include `visit_count:` and `domain:` in frontmatter. Mention repeat visits in the body.

## Skip rules

- Body < 150 words + visit_count == 1.
- Obvious noise (login pages, 404s, cookie banners).
- No extractable entities or concepts from title + URL alone.
