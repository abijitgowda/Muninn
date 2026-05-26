---
name: muninn-ingest-url
description: One-shot URL ingestion. Fetch a URL, extract clean markdown, write a raw file, and run muninn-ingest. Use when the user provides a URL to add to the wiki.
---

# muninn-ingest-url

## Flow

1. Python tool fetches the URL and extracts body to clean markdown.
2. Raw file written to `Raw/Sources/url/<YYYY-MM-DD>/<slug>.md` with frontmatter: `source_type`, `source_url`, `fetched_at`, `title`, `author`, `published_date`, `word_count`.
3. Standard muninn-ingest protocol runs against it.

## How this differs from browser-history

- **User intent is explicit.** Be more generous with extraction even from short articles.
- **No visit_count signal.** Treat as visit_count = 1.
- **Cleaner provenance.** No prior visits or ambiguity about read timing.

## Failure handling

- **Fetch failed (network/403/404):** write raw file with `body: "fetch failed: <reason>"`. Pipeline marks as `failed`. User can retry with `muninn ingest --reprocess <id>`.
- **Extraction returned empty:** save raw HTML, add `extraction: failed` to frontmatter. One-liner source-summary only.
- **Paywalled:** flag in source-summary as partial. Do not create concept pages from a paywall preamble alone.

## URL canonicalization

UTM parameters are stripped before dedup hashing. Fragments are stripped (except for docs pages where fragment = section). So `example.com/foo?utm_source=twitter` and `example.com/foo` are the same URL.

## Bulk variant

```bash
muninn ingest-url --file urls.txt    # one URL per line
```
