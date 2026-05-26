"""`muninn lint` — health checks. Static (no LLM)."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..config import Config
from ..vault import Vault


REQUIRED_FRONTMATTER = {
    "concept": ["title", "kind", "summary", "tags", "created", "updated", "lifecycle"],
    "person": ["title", "kind", "summary", "tags", "created", "updated", "lifecycle"],
    "org": ["title", "kind", "summary", "tags", "created", "updated", "lifecycle"],
    "topic": ["title", "kind", "summary", "tags", "created", "updated", "lifecycle"],
    "project": ["title", "kind", "summary", "tags", "created", "updated", "lifecycle", "status"],
    "source-summary": ["title", "kind", "source_type", "source_ref", "read_date"],
    "doc": ["title", "kind"],
    "redirect": ["title", "kind", "superseded_by"],
}


def run_lint(
    config: Config,
    *,
    consolidate: bool,
    json_out: bool,
    console: Console,
) -> int:
    vault = Vault(config.settings.vault_path)
    findings: dict[str, list[str]] = defaultdict(list)

    pages = vault.all_pages()
    titles_by_lower = {p.title.lower(): p for p in pages if p.kind != "redirect"}
    incoming: Counter[str] = Counter()
    outgoing: dict[Path, list[str]] = {}

    for p in pages:
        links = vault.extract_wikilinks(p.body)
        outgoing[p.path] = links
        for ln in links:
            incoming[ln.lower()] += 1

    # 1. Missing frontmatter
    for p in pages:
        req = REQUIRED_FRONTMATTER.get(p.kind, REQUIRED_FRONTMATTER["doc"])
        missing = [k for k in req if k not in (p.frontmatter or {})]
        if missing:
            findings["missing_frontmatter"].append(f"{_rel(p.path, vault)} missing: {missing}")

    # 2. Broken wikilinks
    for p in pages:
        for link in outgoing[p.path]:
            if link.lower() not in titles_by_lower and not link.startswith("Raw/") and not link.startswith("Wiki/"):
                # Check if it's a path-style link to an existing file
                if not (vault.root / (link + ".md")).exists() and not (vault.root / link).exists():
                    findings["broken_wikilinks"].append(f"{_rel(p.path, vault)} → [[{link}]]")

    # 3. Orphans
    for p in pages:
        if p.kind in ("doc", "redirect", "source-summary"):
            continue
        if incoming[p.title.lower()] == 0:
            findings["orphans"].append(_rel(p.path, vault))

    # 4. Outgoing-link sparsity
    for p in pages:
        if p.kind in ("doc", "redirect", "source-summary"):
            continue
        wc = len(p.body.split())
        if wc > 200 and len(outgoing[p.path]) < 2:
            findings["sparse_links"].append(f"{_rel(p.path, vault)} ({wc} words, {len(outgoing[p.path])} links)")

    # 5. Stale pages
    for p in pages:
        updated = p.frontmatter.get("updated")
        if not updated:
            continue
        try:
            updated_dt = _parse_date(updated)
        except ValueError:
            continue
        sources = p.frontmatter.get("sources") or []
        latest_source_read = _latest_source_read(sources, vault)
        if latest_source_read and latest_source_read > updated_dt:
            findings["stale"].append(
                f"{_rel(p.path, vault)} updated={updated_dt.date()} but newest source read={latest_source_read.date()}"
            )

    # 6. Provenance drift
    for p in pages:
        pv = p.frontmatter.get("provenance") or {}
        if not isinstance(pv, dict):
            continue
        if pv.get("inferred", 0) > 0.5:
            findings["provenance_drift"].append(f"{_rel(p.path, vault)} inferred={pv['inferred']}")
        if pv.get("ambiguous", 0) > 0.3:
            findings["provenance_drift"].append(f"{_rel(p.path, vault)} ambiguous={pv['ambiguous']}")

    # 7. Index drift
    idx_path = vault.wiki / "index.md"
    if idx_path.exists():
        idx_text = idx_path.read_text()
        for p in pages:
            if p.kind not in Vault.INDEX_SECTIONS:
                continue
            section = Vault.INDEX_SECTIONS[p.kind]
            link = f"[[{p.title}]]"
            if f"BEGIN: {section}" in idx_text and link not in idx_text:
                findings["index_drift"].append(f"{_rel(p.path, vault)} not in index [{section}]")

    # 8. Confidence/lifecycle mismatch
    for p in pages:
        life = p.frontmatter.get("lifecycle")
        conf = p.frontmatter.get("confidence")
        if life == "stable" and conf == "low":
            findings["lifecycle_mismatch"].append(f"{_rel(p.path, vault)} stable+low_confidence")

    if json_out:
        print(json.dumps(findings, indent=2, default=str))
        return 1 if any(findings.values()) else 0

    return _render(findings, console, consolidate=consolidate, vault=vault)


def _rel(path: Path, vault: Vault) -> str:
    try:
        return str(path.relative_to(vault.root))
    except ValueError:
        return str(path)


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    s = str(value)
    return datetime.fromisoformat(s) if "T" in s else datetime.strptime(s, "%Y-%m-%d")


def _latest_source_read(sources: list[Any], vault: Vault) -> datetime | None:
    latest: datetime | None = None
    for s in sources or []:
        # s might be "[[title]]" — look up the source-summary page
        if not isinstance(s, str):
            continue
        m = re.search(r"\[\[([^\]]+)\]\]", s)
        title = m.group(1) if m else s
        page = vault.find_page(title)
        if not page:
            continue
        rd = page.frontmatter.get("read_date")
        if not rd:
            continue
        try:
            d = _parse_date(rd)
        except ValueError:
            continue
        latest = max(latest, d) if latest else d
    return latest


def _render(findings: dict[str, list[str]], console: Console, *, consolidate: bool, vault: Vault) -> int:
    if not any(findings.values()):
        console.print("[green]✓ Wiki is clean[/green]")
        return 0
    severity = {
        "missing_frontmatter": "ERROR",
        "index_drift": "ERROR",
        "broken_wikilinks": "WARN",
        "orphans": "WARN",
        "sparse_links": "INFO",
        "stale": "WARN",
        "provenance_drift": "WARN",
        "lifecycle_mismatch": "INFO",
    }
    table = Table(title="Lint findings")
    table.add_column("Severity")
    table.add_column("Category")
    table.add_column("Count", justify="right")
    for cat, items in findings.items():
        table.add_row(severity.get(cat, "INFO"), cat, str(len(items)))
    console.print(table)
    for cat, items in findings.items():
        if not items:
            continue
        console.print(f"\n[bold]{cat}[/bold]")
        for it in items[:25]:
            console.print(f"  • {it}", markup=False, highlight=False)
        if len(items) > 25:
            console.print(f"  … and {len(items) - 25} more")

    has_errors = any(findings[c] for c in findings if severity.get(c) == "ERROR")
    return 1 if has_errors else (0 if not findings else 1)
