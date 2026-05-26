"""Vault helpers — read/write markdown with YAML frontmatter, slugify, append log, index update.

Part of the Muninn local wiki system.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)
from pathlib import Path
from typing import Any

import yaml


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
STRUCTURAL_KINDS = frozenset({"doc", "source-summary", "redirect"})


class _PageCache:
    """In-memory page cache with mtime-based invalidation."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[float, "Page"]] = {}  # path -> (mtime, Page)
        self._title_index: dict[str, Path] = {}  # title.lower() -> path

    def get(self, path: Path) -> "Page | None":
        if path not in self._cache:
            return None
        mtime, page = self._cache[path]
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            self.invalidate(path)
            return None
        if current_mtime != mtime:
            self.invalidate(path)
            return None
        return page

    def put(self, page: "Page") -> None:
        try:
            mtime = page.path.stat().st_mtime
        except OSError:
            return
        self._cache[page.path] = (mtime, page)
        self._title_index[page.title.lower()] = page.path

    def invalidate(self, path: Path) -> None:
        if path in self._cache:
            _, page = self._cache.pop(path)
            self._title_index.pop(page.title.lower(), None)

    def find_by_title(self, title: str) -> "Page | None":
        path = self._title_index.get(title.strip().lower())
        if path:
            return self.get(path)
        return None

    def clear(self) -> None:
        self._cache.clear()
        self._title_index.clear()


@dataclass
class Page:
    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def title(self) -> str:
        return self.frontmatter.get("title") or self.path.stem

    @property
    def kind(self) -> str:
        return self.frontmatter.get("kind", "doc")

    def to_markdown(self) -> str:
        fm = yaml.safe_dump(self.frontmatter, sort_keys=False, allow_unicode=True).strip()
        body = self.body.rstrip() + "\n"
        return f"---\n{fm}\n---\n\n{body}"


class Vault:
    """Wraps the vault directory and provides safe markdown read/write."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Vault not found at {self.root}")
        self._cache = _PageCache()
        self._slug_index: dict[str, Path] | None = None
        self._alias_index: dict[str, Path] | None = None

    def _ensure_slug_index(self) -> dict[str, Path]:
        """Build stem→path and alias→path indices once, O(1) lookups after that."""
        if self._slug_index is not None:
            return self._slug_index
        import time as _time
        t0 = _time.monotonic()
        if self._slug_index is None:
            self._slug_index = {}
            self._alias_index = {}
            if self.wiki.exists():
                for md in self.wiki.rglob("*.md"):
                    self._slug_index[md.stem.lower()] = md
                    page = self.read_page(md)
                    for alias in page.frontmatter.get("aliases", []) or []:
                        alias_key = str(alias).strip().lower()
                        if alias_key:
                            self._alias_index[alias_key] = md
            log.debug("slug_index built: %d pages, %d aliases in %.0fms",
                      len(self._slug_index), len(self._alias_index), (_time.monotonic() - t0) * 1000)
        return self._slug_index

    # ---- paths ----

    @property
    def wiki(self) -> Path:
        return self.root / "Wiki"

    @property
    def raw_sources(self) -> Path:
        return self.root / "Raw" / "Sources"



    def folder_for_kind(self, kind: str) -> Path:
        return {
            "concept": self.wiki / "Concepts",
            "person": self.wiki / "Entities" / "People",
            "org": self.wiki / "Entities" / "Orgs",
            "topic": self.wiki / "Topics",
            "project": self.wiki / "Projects",
            "source-summary": self.wiki / "Sources",
            "doc": self.wiki,
        }.get(kind, self.wiki / "_misc")

    # ---- read ----

    def read_page(self, path: Path) -> Page:
        if not path.is_absolute():
            path = self.root / path
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
                body = m.group(2)
            except yaml.YAMLError:
                fm, body = {}, text
        else:
            fm, body = {}, text
        page = Page(path=path, frontmatter=fm, body=body)
        self._cache.put(page)
        return page

    def page_path(self, kind: str, title: str) -> Path:
        slug = self.slug(title)
        return self.folder_for_kind(kind) / f"{slug}.md"

    def find_page(self, title: str) -> Page | None:
        """Find an existing page by title. O(1) via slug index + page cache."""
        if not title:
            return None
        cached = self._cache.find_by_title(title)
        if cached is not None:
            return cached
        idx = self._ensure_slug_index()
        slug_lower = self.slug(title).lower()
        path = idx.get(slug_lower)
        if path and path.exists():
            return self.read_page(path)
        return self._find_by_alias(title)

    def _find_by_alias(self, title: str) -> Page | None:
        self._ensure_slug_index()
        if self._alias_index is None:
            return None
        path = self._alias_index.get(title.strip().lower())
        if path and path.exists():
            return self.read_page(path)
        return None

    def all_pages(self) -> list[Page]:
        """Load all wiki pages. Uses slug index + page cache to minimize I/O.

        NOTE: This does NOT scale past ~50K pages. At larger scale, callers
        should use ChromaDB queries or paginated iterators instead.
        """
        idx = self._ensure_slug_index()
        pages: list[Page] = []
        for path in idx.values():
            if not path.exists():
                continue
            cached = self._cache.get(path)
            if cached is not None:
                pages.append(cached)
            else:
                pages.append(self.read_page(path))
        return pages

    # ---- write ----

    def write_page(self, page: Page) -> Path:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        page.frontmatter.setdefault("title", page.path.stem)
        page.frontmatter.setdefault("created", datetime.now().date().isoformat())
        page.frontmatter["updated"] = datetime.now().date().isoformat()
        tmp = page.path.with_suffix(".md.tmp")
        tmp.write_text(page.to_markdown(), encoding="utf-8")
        tmp.replace(page.path)
        self._cache.invalidate(page.path)
        self._cache.put(page)
        if self._slug_index is not None:
            self._slug_index[page.path.stem.lower()] = page.path
        return page.path

    def write_raw(
        self,
        connector_name: str,
        item_id: str,
        body: str,
        date: datetime | None = None,
    ) -> Path:
        """Write a raw source file under Raw/Sources/<connector>/<YYYY-MM-DD>/<id>.md."""
        date = date or datetime.now()
        folder = self.raw_sources / connector_name / date.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        safe_id = INVALID_FILENAME.sub("_", item_id)[:120]
        path = folder / f"{safe_id}.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
        return path



    def append_cumulative_log(self, line: str) -> Path:
        month = datetime.now().strftime("%Y-%m")
        path = self.root / ".logs" / f"log-{month}.md"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\ntitle: Log {month}\nkind: doc\ntags: [meta/log]\nlifecycle: pinned\n---\n\n# Log — {month}\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + line.rstrip() + "\n")
        return path

    # ---- index ----

    INDEX_SECTIONS = {
        "concept": "concepts",
        "person": "people",
        "org": "orgs",
        "topic": "topics",
        "project": "projects",
        "source-summary": "sources",
    }

    def update_index_for(self, page: Page) -> None:
        """Add or update an entry in Wiki/index.md for the given page."""
        section_key = self.INDEX_SECTIONS.get(page.kind)
        if not section_key:
            return
        idx = self.wiki / "index.md"
        if not idx.exists():
            return
        text = idx.read_text(encoding="utf-8")
        begin = f"<!-- BEGIN: {section_key} -->"
        end = f"<!-- END: {section_key} -->"
        bi = text.find(begin)
        ei = text.find(end)
        if bi == -1 or ei == -1:
            return  # malformed index; leave it
        block = text[bi + len(begin) : ei]
        link = f"[[{page.title}]]"
        summary = (page.frontmatter.get("summary") or "").replace("\n", " ").strip()
        entry = f"- {link} — {summary}".rstrip(" —")
        # Replace placeholder text, then either replace existing entry or append
        lines = [ln for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("*(no ")]
        replaced = False
        new_lines = []
        for ln in lines:
            if link in ln:
                new_lines.append(entry)
                replaced = True
            else:
                new_lines.append(ln)
        if not replaced:
            new_lines.append(entry)
        new_lines.sort()
        new_block = "\n" + "\n".join(new_lines) + "\n"
        new_text = text[: bi + len(begin)] + new_block + text[ei:]
        idx.write_text(new_text, encoding="utf-8")

    # ---- utility ----

    @staticmethod
    def slug(title: str) -> str:
        """Vault filenames are the natural-language title (no kebab); we only sanitize."""
        s = title.strip()
        s = INVALID_FILENAME.sub("", s)
        s = re.sub(r"\s+", " ", s)
        return s[:180]

    @staticmethod
    def extract_wikilinks(text: str) -> list[str]:
        return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]
