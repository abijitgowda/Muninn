"""`muninn maintain` — weekly housekeeping."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from rich.console import Console

from ..config import Config
from ..vault import Vault
from . import safe_date as _safe_date


def run_maintain(
    config: Config,
    *,
    dry_run: bool,
    cross_link_only: bool,
    console: Console,
) -> None:
    """Weekly: decay + cross-link + index + overview."""
    vault = Vault(config.settings.vault_path)
    pages = vault.all_pages()
    console.print(f"[bold]Maintain[/bold]: {len(pages)} pages")

    inserted = _cross_link(pages, vault, dry_run=dry_run, console=console)
    console.print(f"  cross-link insertions: {inserted}")

    if cross_link_only:
        return

    decayed, archived = _decay_pass(pages, vault, config, dry_run=dry_run, console=console)

    from ..ollama import Ollama
    from .consolidate import _dedup_pass, _supersession_pass
    llm = Ollama(
        host=config.settings.ollama_host,
        model=config.settings.model_for_ingest,
        timeout=config.settings.ollama_timeout,
    )
    merged = _dedup_pass(pages, vault, llm, dry_run=dry_run, console=console)
    superseded = _supersession_pass(pages, vault, dry_run=dry_run, console=console)

    plasticity = _plasticity_pass(pages, vault, llm, config, dry_run=dry_run, console=console)

    # Re-read pages after plasticity may have added/moved files
    if plasticity["total"] > 0 and not dry_run:
        pages = vault.all_pages()

    _rebuild_index(pages, vault, dry_run=dry_run, console=console)
    _rebuild_overview(pages, vault, dry_run=dry_run, console=console)

    # Sync all changes to vectorstore
    if not dry_run:
        try:
            from ..vectorstore import VectorStore
            vs = VectorStore(
                persist_dir=config.settings.state_dir / "chroma",
                ollama_host=config.settings.ollama_host,
            )
            upserted, deleted = vs.sync_vault(pages)
            console.print(f"  vectorstore synced: {upserted} upserted, {deleted} deleted")
        except Exception as e:  # noqa: BLE001
            console.print(f"  [yellow]vectorstore sync skipped: {e}[/yellow]")

    if not dry_run:
        line = (
            f"## [{datetime.now().strftime('%Y-%m-%d %H:%M')}] maintain | weekly | "
            f"pages={len(pages)} cross_links={inserted} deduped={merged} "
            f"superseded={superseded} splits={plasticity['splits']} "
            f"clusters={plasticity['clusters']} reparents={plasticity['reparents']}"
        )
        vault.append_cumulative_log(line)


def run_refresh(config: Config, *, dry_run: bool, console: Console) -> None:
    """Fix all pages in place. No LLM."""
    vault = Vault(config.settings.vault_path)
    pages = vault.all_pages()
    console.print(f"[bold]Refresh[/bold]: {len(pages)} pages")
    _refresh_pages(pages, vault, config, dry_run=dry_run, console=console)


def run_reindex(config: Config, *, dry_run: bool, console: Console) -> None:
    """Archive, re-queue, re-ingest. Wiki stays live."""
    vault = Vault(config.settings.vault_path)
    console.print("[bold]Reindex[/bold]")
    _reindex(vault, config, dry_run=dry_run, console=console)


# ---- cross-linker ----

def _decay_pass(pages, vault: Vault, config: Config, *, dry_run: bool, console: Console) -> tuple[int, int]:
    """Weekly memory decay: reduce strength, archive weak memories."""
    decay_rate = float(getattr(config.settings, "decay_rate", 0.95))
    archive_threshold = float(getattr(config.settings, "archive_threshold", 0.1))
    today = datetime.now().date().isoformat()
    decayed = 0
    archived = 0
    dirty: list = []

    skip_kinds = {"doc", "source-summary", "redirect"}
    for p in pages:
        if p.kind in skip_kinds:
            continue
        if p.frontmatter.get("lifecycle") == "pinned":
            continue

        strength = float(p.frontmatter.get("strength", 1.0))
        access_count = int(p.frontmatter.get("access_count", 0))

        strength *= decay_rate

        if access_count > 0:
            strength = min(strength + 0.1 * access_count, 2.0)
            p.frontmatter["access_count"] = 0

        if p.frontmatter.get("updated") == today:
            strength = min(strength + 0.2, 2.0)

        p.frontmatter["strength"] = round(strength, 3)

        if strength < archive_threshold:
            p.frontmatter["lifecycle"] = "stale"
            archived += 1

        decayed += 1
        dirty.append(p)

    if not dry_run:
        for p in dirty:
            vault.write_page(p)

    console.print(f"  decay pass: {decayed} pages decayed (rate={decay_rate}), {archived} marked stale")
    return decayed, archived


# ---- cross-linker ----

def _cross_link(pages, vault: Vault, *, dry_run: bool, console: Console) -> int:
    _skip_titles = {"overview", "index", "wiki index", "wiki log"}
    titles = sorted(
        ((p.title, p) for p in pages
         if p.kind not in ("redirect", "doc", "source-summary")
         and p.title.lower() not in _skip_titles),
        key=lambda t: -len(t[0]),
    )
    title_patterns = {
        title: re.compile(rf"(?<![\w/\[]){re.escape(title)}(?!\w)", re.IGNORECASE)
        for title, _ in titles if len(title) >= 4
    }
    inserted = 0
    for p in pages:
        if p.frontmatter.get("lifecycle") == "pinned":
            continue
        body = p.body
        already_linked = set(vault.extract_wikilinks(body))
        already_linked_lower = {a.lower() for a in already_linked}
        new_body = body

        skip_ranges: list[tuple[int, int]] = []
        for m in re.finditer(r'```.*?```|`[^`]+`', body, re.DOTALL):
            skip_ranges.append((m.start(), m.end()))
        for m in re.finditer(r'\[[^\]]*\]\([^)]+\)', body):
            skip_ranges.append((m.start(), m.end()))
        for m in re.finditer(r'\[\[[^\]]+\]\]', body):
            skip_ranges.append((m.start(), m.end()))
        for m in re.finditer(r'https?://\S+', body):
            skip_ranges.append((m.start(), m.end()))

        def _in_protected(pos: int, _ranges=skip_ranges) -> bool:
            return any(s <= pos < e for s, e in _ranges)

        for title, target in titles:
            if target.path == p.path:
                continue
            if title.lower() in already_linked_lower:
                continue
            pattern = title_patterns.get(title)
            if not pattern:
                continue
            m = pattern.search(new_body)
            if m and not _in_protected(m.start()):
                new_body = new_body[:m.start()] + f"[[{title}]]" + new_body[m.end():]
                already_linked_lower.add(title.lower())
                inserted += 1
        if new_body != body and not dry_run:
            p.body = new_body
            vault.write_page(p)
    return inserted


# ---- index regen ----

def _rebuild_index(pages, vault: Vault, *, dry_run: bool, console: Console) -> None:
    idx = vault.wiki / "index.md"
    if not idx.exists():
        return
    text = idx.read_text()

    by_section: dict[str, list[str]] = {v: [] for v in Vault.INDEX_SECTIONS.values()}
    for p in pages:
        sec = Vault.INDEX_SECTIONS.get(p.kind)
        if not sec:
            continue
        summary = (p.frontmatter.get("summary") or "").replace("\n", " ").strip()
        by_section[sec].append(f"- [[{p.title}]] — {summary}".rstrip(" —"))

    for sec, entries in by_section.items():
        entries.sort()
        block = "\n" + ("\n".join(entries) if entries else "*(empty)*") + "\n"
        text = _replace_block(text, sec, block)

    if not dry_run:
        idx.write_text(text)
    console.print(f"  index regenerated ({sum(len(v) for v in by_section.values())} entries)")


def _replace_block(text: str, name: str, new_block: str) -> str:
    begin = f"<!-- BEGIN: {name} -->"
    end = f"<!-- END: {name} -->"
    bi = text.find(begin)
    ei = text.find(end)
    if bi == -1 or ei == -1:
        return text
    return text[: bi + len(begin)] + new_block + text[ei:]


# ---- overview regen ----

def _rebuild_overview(pages, vault: Vault, *, dry_run: bool, console: Console) -> None:
    incoming: Counter[str] = Counter()
    for p in pages:
        for ln in vault.extract_wikilinks(p.body):
            incoming[ln] += 1
    top_hubs = incoming.most_common(10)

    cutoff = datetime.now() - timedelta(days=7)
    recent = sorted(
        [p for p in pages if _safe_date(p.frontmatter.get("created")) >= cutoff],
        key=lambda p: _safe_date(p.frontmatter.get("created")),
        reverse=True,
    )

    stale = sorted(
        [
            p
            for p in pages
            if p.frontmatter.get("lifecycle") not in ("pinned",)
            and (datetime.now() - _safe_date(p.frontmatter.get("updated"))).days > 60
        ],
        key=lambda p: _safe_date(p.frontmatter.get("updated")),
    )

    open_qs: list[str] = []
    for p in pages:
        if p.kind == "doc":
            continue
        in_section = False
        for line in p.body.splitlines():
            if line.strip().lower().startswith("## open question"):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section and line.strip().startswith("- "):
                import re as _re2
                clean = _re2.sub(r'\s*\*?\(from\s+.*?\)\*?', '', line.strip())
                if clean and clean != "- ":
                    open_qs.append(f"{clean}  *(from [[{p.title}]])*")

    body = (
        f"# Overview — {datetime.now().date().isoformat()}\n\n"
        "## Top hubs\n"
        + "\n".join(f"- [[{title}]] — {count} incoming links" for title, count in top_hubs)
        + "\n\n## Recent additions (last 7 days)\n"
        + "\n".join(f"- [[{p.title}]] — {p.frontmatter.get('summary', '')}" for p in recent[:20])
        + "\n\n## Stale areas (no update in 60+ days)\n"
        + "\n".join(f"- [[{p.title}]] — updated {p.frontmatter.get('updated')}" for p in stale[:20])
        + "\n\n## Open questions across the wiki\n"
        + ("\n".join(open_qs[:25]) or "*(none recorded)*")
    )

    fm = {
        "title": "Overview",
        "kind": "doc",
        "tags": ["meta/overview"],
        "lifecycle": "pinned",
        "updated": datetime.now().date().isoformat(),
    }
    from ..vault import Page

    page = Page(path=vault.wiki / "overview.md", frontmatter=fm, body=body)
    if not dry_run:
        vault.write_page(page)
    console.print(f"  overview rebuilt (top hubs: {len(top_hubs)}, recent: {len(recent)}, stale: {len(stale)})")



def _reindex(vault: Vault, config: Config, *, dry_run: bool, console: Console) -> None:
    """Archive wiki, re-queue all items, and re-ingest. Wiki stays live — pages are merged, not recreated."""
    import shutil

    from ..manifest import Manifest

    count = Manifest(config.settings.manifest_path)._conn.execute(
        "SELECT COUNT(*) FROM items WHERE status IN ('processed', 'skipped')"
    ).fetchone()[0]

    if dry_run:
        console.print(f"  [yellow]dry-run: would archive wiki and re-queue {count} items[/yellow]")
        return

    # Snapshot current wiki for rollback
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_dir = vault.root / ".muninn" / "archives" / ts
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(vault.wiki, archive_dir / "Wiki")
    console.print(f"  [green]archived Wiki/ → .muninn/archives/{ts}/[/green]")

    manifest = Manifest(config.settings.manifest_path)
    manifest._conn.execute(
        "UPDATE items SET status = 'pending' WHERE status IN ('processed', 'skipped')"
    )
    manifest._conn.commit()
    console.print(f"  [green]re-queued {count} items[/green]")

    from .ingest import run_ingest
    run_ingest(
        config,
        source_name=None,
        all_sources=True,
        limit=None,
        dry_run=False,
        reprocess=None,
        verbose=True,
        console=console,
    )


def _refresh_pages(pages, vault: Vault, config: Config, *, dry_run: bool, console: Console) -> None:
    """Update all pages in place — no LLM, no re-extraction. Fixes structural issues."""
    import re as _re

    from ..provenance import compute_provenance, extract_inline_provenance

    skip_kinds = {"doc", "source-summary", "redirect"}
    knowledge = [p for p in pages if p.kind not in skip_kinds]
    console.print(f"  refreshing {len(knowledge)} knowledge pages...")

    all_titles = {p.title.lower() for p in pages}
    fixed = 0
    for p in knowledge:
        changed = False
        schema = str(p.frontmatter.get("schema", "general"))

        # 1. Derive tags from schema + kind
        expected_tags = [seg for seg in schema.split("/") if seg] + [p.kind]
        if p.frontmatter.get("tags") != expected_tags:
            p.frontmatter["tags"] = expected_tags
            changed = True

        # 2. Ensure required frontmatter fields exist
        for field, default in [
            ("confidence", "medium"),
            ("lifecycle", "draft"),
            ("strength", 1.0),
            ("access_count", 0),
            ("consolidation_count", 0),
        ]:
            if field not in p.frontmatter:
                p.frontmatter[field] = default
                changed = True

        # 3. Recompute provenance from inline markers
        markers = extract_inline_provenance(p.body)
        if markers:
            prov = compute_provenance(markers)
            if p.frontmatter.get("provenance") != prov:
                p.frontmatter["provenance"] = prov
                changed = True
        elif "provenance" not in p.frontmatter:
            p.frontmatter["provenance"] = {"extracted": 0.8, "inferred": 0.2, "ambiguous": 0.0}
            changed = True

        # 4. Fix broken wikilinks (nested, inside URLs, empty, dangling)
        body = p.body
        body = _re.sub(r'\[\[\s*\]\]', '', body)
        body = _re.sub(r'(https?://[^\s]*?)\[\[([^\]]+)\]\]', lambda m: m.group(1) + m.group(2), body)
        body = _re.sub(r'(\[\[[^\]]*?)\[\[([^\]]+)\]\]', lambda m: m.group(1) + m.group(2), body)
        from ..vault import WIKILINK_RE as _WL_RE
        def _strip_dangling(m):
            target = m.group(1).strip()
            if not target:
                return ''
            if target.lower() in all_titles:
                return m.group(0)
            return m.group(2) if m.group(2) else target
        body = _WL_RE.sub(_strip_dangling, body)
        if body != p.body:
            p.body = body
            changed = True

        if changed and not dry_run:
            vault.write_page(p)
            fixed += 1

    console.print(f"  frontmatter/wikilinks fixed: {fixed} pages")

    # 5. Cross-link
    inserted = _cross_link(pages, vault, dry_run=dry_run, console=console)
    console.print(f"  cross-link insertions: {inserted}")

    # 6. Rebuild index + overview
    _rebuild_index(pages, vault, dry_run=dry_run, console=console)
    _rebuild_overview(pages, vault, dry_run=dry_run, console=console)

    # 7. Clean dangling wikilinks from ALL pages (run after index rebuild)
    if not dry_run:
        from ..vault import WIKILINK_RE as _WL_RE2
        structural_fixed = 0
        for md in vault.wiki.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            original = text
            text = _re.sub(r'\[\[\s*\]\]', '', text)
            def _strip_d(m):
                target = m.group(1).strip()
                if not target:
                    return ''
                if target.lower() in all_titles:
                    return m.group(0)
                return m.group(2) if m.group(2) else target
            text = _WL_RE2.sub(_strip_d, text)
            if text != original:
                md.write_text(text, encoding="utf-8")
                structural_fixed += 1
        if structural_fixed:
            console.print(f"  dangling wikilinks cleaned: {structural_fixed} pages")

    # 7. Sync vectorstore
    if not dry_run:
        try:
            from ..vectorstore import VectorStore
            vs = VectorStore(
                persist_dir=config.settings.state_dir / "chroma",
                ollama_host=config.settings.ollama_host,
            )
            upserted, deleted = vs.sync_vault(pages)
            console.print(f"  vectorstore synced: {upserted} upserted, {deleted} deleted")
        except ImportError:
            pass

    console.print("[bold green]Refresh done.[/bold green]")


# ---------------------------------------------------------------------------
# Brain plasticity — structural reorganization
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_SKIP_SECTIONS = frozenset({"sources", "synthesis", "open questions", "references", "see also"})


def _plasticity_pass(
    pages: list,
    vault: Vault,
    llm,
    config: Config,
    *,
    dry_run: bool,
    console: Console,
) -> dict[str, int]:
    """Brain plasticity: split overgrown pages, spawn cluster abstractions, reparent misclassified pages."""
    from ..vault import STRUCTURAL_KINDS

    knowledge = [p for p in pages if p.kind not in STRUCTURAL_KINDS and p.frontmatter.get("lifecycle") != "pinned"]
    stats = {"splits": 0, "clusters": 0, "reparents": 0, "total": 0}
    new_pages: list = []
    moved_pages: list[tuple] = []  # (old_path, new_page)

    # ---- 1. Split overgrown pages ----
    for page in knowledge:
        if not _should_split(page):
            continue
        if dry_run:
            console.print(f"  plasticity/split: {page.title} ({len(page.body.split())} words, {len(_content_sections(page.body))} sections)")
            stats["splits"] += 1
            continue
        sub_pages = _split_page(page, vault, llm)
        if sub_pages:
            stats["splits"] += 1
            new_pages.extend(sub_pages)
            console.print(f"  plasticity/split: {page.title} → {len(sub_pages)} sub-pages")

    # ---- 2. Cluster abstraction ----
    clusters = _detect_clusters(knowledge, vault)
    for cluster_pages, shared_links in clusters[:3]:
        titles = [p.title for p in cluster_pages]
        if dry_run:
            console.print(f"  plasticity/cluster: [{', '.join(titles[:4])}] ({len(shared_links)} shared links)")
            stats["clusters"] += 1
            continue
        new_page = _spawn_cluster(cluster_pages, shared_links, vault, llm)
        if new_page:
            stats["clusters"] += 1
            new_pages.append(new_page)
            console.print(f"  plasticity/cluster: spawned [[{new_page.title}]] from {len(cluster_pages)} pages")

    # ---- 3. Reparent misclassified pages ----
    for page in knowledge:
        new_kind = _detect_reparent(page, vault)
        if not new_kind:
            continue
        if dry_run:
            console.print(f"  plasticity/reparent: {page.title} ({page.kind} → {new_kind})")
            stats["reparents"] += 1
            continue
        old_path = page.path
        _reparent_page(page, new_kind, vault)
        moved_pages.append((old_path, page))
        stats["reparents"] += 1
        console.print(f"  plasticity/reparent: {page.title} → {new_kind}")

    stats["total"] = stats["splits"] + stats["clusters"] + stats["reparents"]

    # ---- Sync ChromaDB + manifest relationships ----
    if stats["total"] > 0 and not dry_run:
        _sync_plasticity(new_pages, moved_pages, vault, config, console)

    if stats["total"]:
        console.print(f"  plasticity: {stats['splits']} splits, {stats['clusters']} clusters, {stats['reparents']} reparents")
    return stats


def _sync_plasticity(
    new_pages: list,
    moved_pages: list[tuple],
    vault: Vault,
    config: Config,
    console: Console,
) -> None:
    """Update ChromaDB and manifest after plasticity operations."""
    from ..manifest import Manifest

    manifest = Manifest(config.settings.manifest_path)

    # Record relationships for split pages (hub → sub-page)
    for page in new_pages:
        split_from = page.frontmatter.get("split_from", "").strip("[]")
        if split_from:
            manifest.add_relationship(split_from, page.title, "split_into", confidence="high")

        cluster_from = page.frontmatter.get("cluster_from", [])
        for member in cluster_from:
            manifest.add_relationship(page.title, member, "abstracts", confidence="medium")

    # Vectorstore sync happens in run_maintain after plasticity returns


# ---- Split ----

def _content_sections(body: str) -> list[tuple[str, str]]:
    """Extract (heading, content) pairs for ## sections, skipping meta sections."""
    sections = []
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        if heading.lower() in _SKIP_SECTIONS:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        if len(content.split()) >= 50:
            sections.append((heading, content))
    return sections


def _should_split(page) -> bool:
    word_count = len(page.body.split())
    if word_count < 2000:
        return False
    sections = _content_sections(page.body)
    if len(sections) < 4:
        return False
    if float(page.frontmatter.get("strength", 0)) < 0.5:
        return False
    if int(page.frontmatter.get("consolidation_count", 0)) < 1:
        return False
    return True


def _split_page(page, vault: Vault, llm) -> list:
    """Split an overgrown page into focused sub-pages. Original becomes a hub."""
    from ..vault import Page

    sections = _content_sections(page.body)
    today = datetime.now().date().isoformat()
    sub_pages = []

    for heading, content in sections:
        sub_title = f"{page.title} — {heading}"
        existing = vault.find_page(sub_title)
        if existing:
            continue

        sub_fm = {
            "title": sub_title,
            "kind": page.kind,
            "schema": page.frontmatter.get("schema", "general"),
            "summary": f"{heading} section from {page.title}.",
            "tags": list(page.frontmatter.get("tags") or []),
            "strength": float(page.frontmatter.get("strength", 1.0)),
            "sources": list(page.frontmatter.get("sources") or []),
            "provenance": page.frontmatter.get("provenance", {}),
            "confidence": page.frontmatter.get("confidence", "medium"),
            "lifecycle": "draft",
            "consolidation_count": 0,
            "access_count": 0,
            "created": today,
            "split_from": f"[[{page.title}]]",
        }
        sub_body = f"# {sub_title}\n\n{content}\n"
        sub_path = vault.page_path(page.kind, sub_title)
        sub_page = Page(path=sub_path, frontmatter=sub_fm, body=sub_body)
        vault.write_page(sub_page)
        sub_pages.append(sub_page)

    if sub_pages:
        hub_lines = [f"# {page.title}\n"]
        summary = page.frontmatter.get("summary", "")
        if summary:
            hub_lines.append(f"{summary}\n")
        hub_lines.append("## Contents\n")
        for sp in sub_pages:
            hub_lines.append(f"- [[{sp.title}]]")

        # Keep synthesis section if it exists
        synth_match = re.search(r"(## Synthesis\b.*)", page.body, re.DOTALL)
        if synth_match:
            hub_lines.append(f"\n{synth_match.group(1).strip()}")

        page.body = "\n".join(hub_lines) + "\n"
        page.frontmatter["lifecycle"] = "stable"
        vault.write_page(page)

    return sub_pages


# ---- Cluster abstraction ----

def _detect_clusters(pages: list, vault: Vault) -> list[tuple[list, set[str]]]:
    """Find groups of 3+ pages that share >50% of outgoing wikilinks."""
    from ..vault import WIKILINK_RE

    link_sets: dict[str, set[str]] = {}
    page_map: dict[str, any] = {}
    for p in pages:
        links = set(WIKILINK_RE.findall(p.body))
        link_titles = {m[0].strip().lower() for m in WIKILINK_RE.finditer(p.body) if m} if not links else set()
        # Re-extract properly
        link_titles = {m.group(1).strip().lower() for m in WIKILINK_RE.finditer(p.body)}
        if len(link_titles) >= 3:
            link_sets[p.title.lower()] = link_titles
            page_map[p.title.lower()] = p

    if len(link_sets) < 3:
        return []

    {p.title.lower() for p in pages}
    clusters: list[tuple[list, set[str]]] = []
    used: set[str] = set()

    titles = list(link_sets.keys())
    for i, t1 in enumerate(titles):
        if t1 in used:
            continue
        cluster = [t1]
        shared = link_sets[t1].copy()
        for t2 in titles[i + 1:]:
            if t2 in used:
                continue
            overlap = link_sets[t1] & link_sets[t2]
            union = link_sets[t1] | link_sets[t2]
            if len(overlap) / len(union) > 0.4:
                cluster.append(t2)
                shared &= link_sets[t2]

        if len(cluster) >= 3 and len(shared) >= 2:
            # Check no existing page already serves as the cluster hub
            cluster_pages = [page_map[t] for t in cluster]
            hub_exists = False
            for p in cluster_pages:
                if int(p.frontmatter.get("consolidation_count", 0)) >= 3:
                    hub_exists = True
                    break
            if not hub_exists:
                clusters.append((cluster_pages, shared))
                used.update(cluster)

    clusters.sort(key=lambda c: -len(c[0]))
    return clusters


def _spawn_cluster(cluster_pages: list, shared_links: set[str], vault: Vault, llm):
    """Ask LLM to name and describe a cluster, then create an abstract parent page."""
    from ..vault import Page

    titles = [p.title for p in cluster_pages]
    summaries = [f"- {p.title}: {p.frontmatter.get('summary', '')}" for p in cluster_pages]

    system = (
        "You are organizing a knowledge graph. Given a cluster of related wiki pages, "
        "create an abstract parent concept that ties them together. "
        'Return JSON: {"title": "...", "summary": "one sentence", "body": "2-3 paragraphs with [[wikilinks]] to the cluster pages"}'
    )
    user = "Cluster pages:\n" + "\n".join(summaries)

    try:
        result = llm.chat_json(system, user, temperature=0.1, num_ctx=4096)
    except Exception:
        return None

    title = result.get("title", "").strip()
    if not title or vault.find_page(title):
        return None

    today = datetime.now().date().isoformat()
    fm = {
        "title": title,
        "kind": "concept",
        "schema": "general",
        "summary": result.get("summary", ""),
        "tags": ["concept"],
        "strength": 1.0,
        "sources": [f"[[{t}]]" for t in titles],
        "provenance": {"extracted": 0.0, "inferred": 1.0, "ambiguous": 0.0},
        "confidence": "medium",
        "lifecycle": "draft",
        "consolidation_count": 0,
        "access_count": 0,
        "created": today,
        "cluster_from": titles,
    }
    body = f"# {title}\n\n{result.get('body', '')}\n"
    path = vault.page_path("concept", title)
    page = Page(path=path, frontmatter=fm, body=body)
    vault.write_page(page)
    return page


# ---- Reparent ----

_KIND_TO_FOLDER_STEM = {
    "concept": "Concepts",
    "person": "People",
    "org": "Orgs",
    "topic": "Topics",
    "project": "Projects",
    "source-summary": "Sources",
}


def _detect_reparent(page, vault: Vault) -> str | None:
    """Detect if a page's folder doesn't match its kind."""
    expected_folder = _KIND_TO_FOLDER_STEM.get(page.kind)
    if not expected_folder:
        return None
    if expected_folder in str(page.path):
        return None

    # Infer correct kind from current folder
    for kind, folder in _KIND_TO_FOLDER_STEM.items():
        if folder in str(page.path) and kind != page.kind:
            return None  # folder is consistent with some other kind — don't touch

    # Page kind says X but it's not in the X folder — move it
    return page.kind


def _reparent_page(page, new_kind: str, vault: Vault) -> None:
    """Move a page to the correct folder for its kind."""
    import shutil

    new_path = vault.page_path(new_kind, page.title)
    if new_path == page.path or new_path.exists():
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(page.path), str(new_path))
    page.path = new_path
    vault.write_page(page)

