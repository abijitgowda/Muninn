"""`muninn consolidate` — nightly consolidation cycle.

Each recently-touched page is read, re-synthesised by the LLM, and written
back with a cleaner body and updated metadata.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from rich.console import Console

from ..config import Config
from ..ollama import Ollama, OllamaError
from ..vault import Page, Vault
from . import safe_date as _safe_date

# Kinds that should never be consolidated — they are structural/meta pages.
_SKIP_KINDS = frozenset({"doc", "source-summary", "redirect"})

# Importance ranking used for sort order.
_IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_consolidate(
    config: Config,
    *,
    dry_run: bool = False,
    page_title: str | None = None,
    console: Console,
) -> None:
    """LLM rewrite of knowledge pages — strengthen claims, merge addenda, synthesize."""
    vault = Vault(config.settings.vault_path)
    pages = vault.all_pages()
    console.print(f"[bold]Consolidate[/bold]: {len(pages)} pages in vault")

    candidates = _select_candidates(pages, page_title=page_title)

    if not candidates:
        console.print("  no consolidation candidates found")
        return

    console.print(f"  candidates: {len(candidates)}")

    llm = Ollama(
        host=config.settings.ollama_host,
        model=config.settings.model_for_ingest,
        timeout=config.settings.ollama_timeout,
    )

    if dry_run:
        for i, page in enumerate(candidates, 1):
            sources = page.frontmatter.get("sources") or []
            label = "synthesis" if len(sources) >= 5 else "standard"
            console.print(
                f"  [{i}/{len(candidates)}] {page.title} — "
                f"{len(sources)} source(s), {label} — [yellow]dry-run[/yellow]"
            )
        return

    consolidated_count = 0
    abstracted_count = 0

    for i, page in enumerate(candidates, 1):
        sources = page.frontmatter.get("sources") or []
        source_count = len(sources)
        is_synthesis = source_count >= 5

        try:
            new_body = _consolidate_page(llm, page, is_synthesis=is_synthesis)
        except OllamaError as exc:
            console.print(
                f"  [red][{i}/{len(candidates)}] {page.title} — LLM error: {exc}[/red]"
            )
            continue

        if not _validate_output(new_body, page):
            console.print(
                f"  [red][{i}/{len(candidates)}] {page.title} — invalid output, skipped[/red]"
            )
            continue

        page.body = new_body
        page.frontmatter["consolidation_count"] = int(page.frontmatter.get("consolidation_count", 0)) + 1
        page.frontmatter["updated"] = datetime.now().date().isoformat()

        if is_synthesis and source_count >= 3:
            page.frontmatter["lifecycle"] = "stable"

        vault.write_page(page)

        tag = "synthesis" if is_synthesis else "standard"
        console.print(
            f"  [{i}/{len(candidates)}] {page.title} — "
            f"{source_count} source(s), {tag} — [green]consolidated[/green]"
        )
        consolidated_count += 1
        if is_synthesis:
            abstracted_count += 1

    # Sync rewritten pages to vectorstore
    if consolidated_count > 0:
        try:
            from ..vectorstore import VectorStore
            vs = VectorStore(
                persist_dir=config.settings.state_dir / "chroma",
                ollama_host=config.settings.ollama_host,
            )
            refreshed = vault.all_pages()
            upserted, deleted = vs.sync_vault(refreshed)
            console.print(f"  vectorstore synced: {upserted} upserted, {deleted} deleted")
        except Exception as e:  # noqa: BLE001
            console.print(f"  [yellow]vectorstore sync skipped: {e}[/yellow]")

    line = (
        f"## [{datetime.now().strftime('%Y-%m-%d %H:%M')}] consolidate | "
        f"consolidated {consolidated_count}, abstracted {abstracted_count}"
    )
    vault.append_cumulative_log(line)

    console.print(
        f"\n[bold green]Done[/bold green]: consolidated {consolidated_count}, "
        f"abstracted {abstracted_count}"
    )


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _select_candidates(
    pages: list[Page],
    *,
    page_title: str | None = None,
) -> list[Page]:
    """Return pages eligible for consolidation, sorted by importance then source count."""

    if page_title:
        return [p for p in pages if p.title.lower() == page_title.lower()]

    cutoff = datetime.now() - timedelta(hours=24)
    candidates: list[Page] = []

    for p in pages:
        # Skip structural / meta kinds.
        if p.kind in _SKIP_KINDS:
            continue
        # Skip pinned pages — they are manually curated.
        if p.frontmatter.get("lifecycle") == "pinned":
            continue

        updated = _safe_date(p.frontmatter.get("updated"))
        strength = float(p.frontmatter.get("strength", 0.0))
        consolidation_count = int(p.frontmatter.get("consolidation_count", 0))

        recently_updated = updated >= cutoff
        fresh_and_strong = strength > 0.5 and consolidation_count == 0

        if recently_updated or fresh_and_strong:
            candidates.append(p)

    # Sort: high importance first, then by descending source count.
    candidates.sort(
        key=lambda p: (
            _IMPORTANCE_RANK.get(p.frontmatter.get("importance", "low"), 2),
            -(len(p.frontmatter.get("sources") or [])),
        ),
    )
    return candidates


# ---------------------------------------------------------------------------
# Brain: pattern separation (dedup pass)
# ---------------------------------------------------------------------------

def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _words(text: str) -> set[str]:
    """Lowercase word set for Jaccard comparison."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _dedup_pass(
    pages: list[Page],
    vault: Vault,
    ollama: Ollama,
    *,
    dry_run: bool,
    console: Console,
) -> int:
    """Find near-duplicate knowledge pages and merge them.

    Uses cheap Jaccard similarity on word-sets (title and summary) to avoid
    O(n^2) embedding calls.  Candidate pairs are verified by an LLM before
    merging.
    """
    # Only consider knowledge pages (skip structural kinds).
    knowledge = [p for p in pages if p.kind not in _SKIP_KINDS]

    # Build lookup keyed on normalised title.
    by_title: dict[str, Page] = {}
    for p in knowledge:
        key = p.title.lower().strip()
        # If two pages share exact title keep the first one encountered.
        by_title.setdefault(key, p)

    unique_pages = list(by_title.values())
    found = 0
    merged = 0

    # Build inverted index: word -> list of pages containing that word in title
    word_to_pages: dict[str, list[Page]] = defaultdict(list)
    for p in unique_pages:
        for w in _words(p.title):
            word_to_pages[w].append(p)

    # Only compare pages sharing at least one title word
    checked: set[frozenset[int]] = set()
    candidates: list[tuple[Page, Page, float]] = []
    for p in unique_pages:
        for w in _words(p.title):
            for other in word_to_pages[w]:
                if other is p:
                    continue
                pair = frozenset([id(p), id(other)])
                if pair in checked:
                    continue
                checked.add(pair)
                # compute similarity
                title_sim = _jaccard(_words(p.title), _words(other.title))
                if title_sim > 0.6:
                    candidates.append((p, other, title_sim))
                else:
                    summary_sim = _jaccard(
                        _words(p.frontmatter.get("summary", "")),
                        _words(other.frontmatter.get("summary", "")),
                    )
                    if summary_sim > 0.7:
                        candidates.append((p, other, summary_sim))

    for page_a, page_b, score in candidates:
        found += 1

        if dry_run:
            console.print(
                f"  dedup candidate: '{page_a.title}' ↔ '{page_b.title}' "
                f"(sim={score:.2f})"
            )
            continue

        # Ask LLM to confirm.
        prompt_system = (
            "You are a deduplication judge. Given two wiki page titles and "
            "summaries, decide if they are about the same topic. Respond in "
            'JSON: {"same": true/false, "canonical": "A" or "B"}'
        )
        prompt_user = (
            f"Page A: '{page_a.title}' — {page_a.frontmatter.get('summary', '')}\n"
            f"Page B: '{page_b.title}' — {page_b.frontmatter.get('summary', '')}"
        )

        try:
            result = ollama.chat_json(
                prompt_system, prompt_user,
                temperature=0, num_ctx=2048,
            )
        except Exception:
            # Graceful failure: skip this pair on any LLM error.
            continue

        if not result.get("same"):
            continue

        # Determine canonical vs non-canonical.
        if result.get("canonical", "A").upper() == "B":
            canonical, non_canonical = page_b, page_a
        else:
            canonical, non_canonical = page_a, page_b

        # Merge non-canonical into canonical.
        canonical.body = canonical.body.rstrip() + "\n\n" + non_canonical.body.lstrip()

        # Merge sources lists.
        can_sources = list(canonical.frontmatter.get("sources") or [])
        nc_sources = list(non_canonical.frontmatter.get("sources") or [])
        seen = set(can_sources)
        for src in nc_sources:
            if src not in seen:
                can_sources.append(src)
                seen.add(src)
        canonical.frontmatter["sources"] = can_sources

        # Mark non-canonical as redirect.
        non_canonical.frontmatter["kind"] = "redirect"
        non_canonical.frontmatter["lifecycle"] = "superseded"
        non_canonical.frontmatter["superseded_by"] = f"[[{canonical.title}]]"
        non_canonical.body = f"This page has moved to [[{canonical.title}]]."

        vault.write_page(canonical)
        vault.write_page(non_canonical)
        merged += 1

    console.print(f"  dedup: found {found} duplicates, merged {merged}")
    return merged


# ---------------------------------------------------------------------------
# Brain: active forgetting (supersession pass)
# ---------------------------------------------------------------------------

def _supersession_pass(
    pages: list[Page],
    vault: Vault,
    *,
    dry_run: bool,
    console: Console,
) -> int:
    """Mark weak, unvisited draft pages as stale (active forgetting).

    Any page with lifecycle=draft, strength < 0.2, and last_accessed null
    or > 30 days ago is moved to lifecycle=stale.  The regular decay pass
    will eventually push these below the archive threshold.
    """
    cutoff = datetime.now() - timedelta(days=30)
    superseded = 0

    for page in pages:
        if page.kind in _SKIP_KINDS:
            continue
        if page.frontmatter.get("lifecycle") != "draft":
            continue
        strength = float(page.frontmatter.get("strength", 0.0))
        if strength >= 0.2:
            continue

        last_accessed = _safe_date(page.frontmatter.get("last_accessed"))
        # _safe_date returns epoch (1970) when value is None/empty — that counts
        # as "never accessed", which qualifies.
        if last_accessed > cutoff:
            continue

        if dry_run:
            console.print(
                f"  forgetting candidate: '{page.title}' "
                f"(strength={strength:.2f}, lifecycle=draft)"
            )
        else:
            page.frontmatter["lifecycle"] = "stale"
            vault.write_page(page)

        superseded += 1

    console.print(f"  forgetting: superseded {superseded} stale pages")
    return superseded


# ---------------------------------------------------------------------------
# LLM consolidation
# ---------------------------------------------------------------------------

_STANDARD_SYSTEM = """\
You are consolidating a wiki page. Strengthen what matters, let go of noise, \
form an abstract understanding.

Rules:
- Preserve ALL [[wikilinks]] and sources in the frontmatter
- Promote high-confidence claims (multiple sources) to the top of each section
- Demote or remove redundant/trivial claims
- If claims repeat across addendum sections, merge them into the main body
- Preserve inline provenance markers: ^[extracted], ^[inferred], ^[ambiguous]
- If a claim has been corroborated by a new source, it is ^[extracted] (drop ^[inferred])
- If claims from different sources disagree, mark with ^[ambiguous]
- Add a "## Synthesis" section at the end: 2-3 sentences capturing the abstract gist
- Output the FULL page body (everything after the frontmatter), nothing else"""

_ABSTRACTION_SYSTEM = """\
This page has been updated from {source_count} sources over {days} days.
Write a DEFINITIVE page that captures what is truly known:
- Keep only claims supported by 2+ sources
- Promote ^[extracted] claims (directly from sources) to the top
- Preserve ^[inferred] claims only if they add genuine insight
- Mark disagreements with ^[ambiguous] and note which sources disagree
- State the abstract principle, not specific article wording
- List the 3 most important open questions
- Add a "## Synthesis" section: the core insight in 2-3 sentences
- Output the FULL page body, nothing else

IMPORTANT: Remove all <!-- ingest:... --> addendum blocks.
Absorb their claims into the main body sections.
The result should read as a definitive, standalone page — \
not a chronological log of additions.
Strip specific article titles, dates-of-reading, and "From [[source]]" headers.
Keep the claims and facts; lose the ingestion scaffolding."""


def _consolidate_page(
    llm: Ollama,
    page: Page,
    *,
    is_synthesis: bool,
) -> str:
    """Send the page body to the LLM and return the consolidated body."""

    if is_synthesis:
        created = _safe_date(page.frontmatter.get("created"))
        days = max((datetime.now() - created).days, 1)
        source_count = len(page.frontmatter.get("sources") or [])
        system = _ABSTRACTION_SYSTEM.format(source_count=source_count, days=days)
    else:
        system = _STANDARD_SYSTEM

    new_body = llm.chat(
        system=system,
        user=page.body,
        temperature=0.1,
        num_ctx=8192,
    )

    # Episodic→semantic: strip any remaining ingest markers the LLM may have kept.
    if is_synthesis:
        new_body = re.sub(r'<!-- ingest:.*?-->\n?', '', new_body)

    return new_body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _validate_output(new_body: str, original: Page) -> bool:
    """Reject LLM output that looks like garbage.

    Checks:
    1. Non-empty (at least 50 chars of real content)
    2. Preserves at least half of the original wikilinks
    3. Does not contain YAML frontmatter fences (the LLM should only return body)
    """
    stripped = new_body.strip()

    # Must have meaningful content.
    if len(stripped) < 50:
        return False

    # Must not echo back frontmatter.
    if stripped.startswith("---"):
        return False

    # Check wikilink preservation.
    original_links = set(_WIKILINK_RE.findall(original.body))
    if original_links:
        new_links = set(_WIKILINK_RE.findall(new_body))
        preserved = len(original_links & new_links)
        # Require at least half of original links to survive.
        if preserved < len(original_links) / 2:
            return False

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_importance(page: Page) -> str:
    """Compute importance tier from source count and lifecycle."""
    source_count = len(page.frontmatter.get("sources") or [])
    if page.frontmatter.get("lifecycle") == "pinned" or source_count >= 5:
        return "high"
    if source_count >= 2:
        return "medium"
    return "low"

