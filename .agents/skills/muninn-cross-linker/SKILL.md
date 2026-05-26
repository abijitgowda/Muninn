---
name: muninn-cross-linker
description: Scan the vault for unlinked mentions of existing page titles and insert [[wikilinks]]. Use after a batch ingest or as part of maintain.
---

# muninn-cross-linker

Find places where a page mentions another page's title or alias without linking to it. Insert the `[[wikilink]]`.

## Linking rules

- Link the **first mention** per page only (subsequent mentions stay plain text).
- Match titles and `aliases:` from frontmatter. Case-sensitive for proper nouns, case-insensitive for pluralized common nouns.
- Use `[[Title|alias]]` when the matched text is an alias.

## Where to skip linking

- Inside existing `[[...]]`, code blocks, inline code, or frontmatter.
- Inside headings (`# ...`) or callouts (`> [!info]`).
- Titles of 3 characters or fewer (e.g., "AI") unless the target page has `force_link: true`.
- Self-links (a page linking to itself).
- Titles on the exclusion list in `Schema/cross-linker-exclusions.md`.

## Page-type behavior

- **Pinned pages** (`lifecycle: pinned`): link only on verbatim title match, no alias matching.
- **Source-summary and stub pages**: full treatment (they need connectivity most).

## Conflict resolution

When a name matches both an alias and another page's title, link to the longest exact match.
