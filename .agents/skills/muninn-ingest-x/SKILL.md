---
name: muninn-ingest-x
description: Connector-specific guidance for ingesting X (formerly Twitter) posts, threads, replies, and quote-posts. Use when processing raw files with source_type x.
---

# muninn-ingest-x

Raw item frontmatter includes `author`, `post_id`, `thread_id`, `metrics` (likes/reposts/replies), `urls`, `mentions`, and `in_reply_to`.

## Interaction types

- **Standalone post** (`thread_id == post_id`, `in_reply_to: null`): single source. One concept + one entity is usually enough from 280 chars.
- **Thread** (`thread_id != post_id`): connector concatenates the full thread into one raw item. Treat as one source; threads of 5+ posts warrant full extraction.
- **Reply**: parent is included by the connector. Treat parent as primary, reply as commentary.
- **Quote-post**: carries parent context. Ingest both.

## Engagement metrics

Use metrics to calibrate confidence:
- 10K+ likes from a notable account -> stronger signal, `confidence: medium` or higher.
- Low engagement -> `confidence: low`.

## Linked URLs

If `urls:` is non-empty and the linked URL is not yet ingested, queue it. The post is "discovery context"; the linked article is the substantive source.

## What to extract

- **Opinions** -> claim with `^[ambiguous]`, `confidence: medium`.
- **Cited statistics** -> `^[extracted]` if sourced, `^[inferred]` if unsourced number.
- **Announcements** (launches, papers) -> create/update entity page with the date.
- **Pure reactions** -> skip body; optionally note author engagement on relevant entity page.

## Source-summary extras

Include `author:`, `author_handle:`, `post_url:`, and `metrics:` in frontmatter.

## Skip rules

- Posts < 20 chars of substantive text.
- Pure reposts (not quote-posts).
- Posts that are 100% URL -- ingest the URL directly instead.
