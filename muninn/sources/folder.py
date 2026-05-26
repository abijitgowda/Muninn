"""Folder connector for Muninn — recursively walks a local folder and extracts text."""

from __future__ import annotations

import csv
import io
import logging
import shutil
import subprocess
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import SLUG_RE, RawItem, Source

log = logging.getLogger(__name__)

# Extensions handled by explicit extractors.
_TEXT_EXTS = frozenset({".txt", ".md", ".markdown"})
_BINARY_STUB_EXTS = frozenset({".png", ".jpg", ".jpeg", ".heic", ".mp4", ".mov"})

_DEFAULT_INCLUDE = ".pdf,.txt,.md,.docx,.doc,.xlsx,.csv"
_DEFAULT_SKIP = ".png,.jpg,.jpeg,.heic,.mp4,.mov,.zip,.DS_Store"
_DEFAULT_MAX_DEPTH = 3


def _parse_ext_list(raw: str | list) -> set[str]:
    """Normalise a list or comma-separated string of extensions into a set of lowercased exts."""
    items = raw if isinstance(raw, list) else raw.split(",")
    exts: set[str] = set()
    for part in items:
        part = str(part).strip().lower()
        if not part:
            continue
        if not part.startswith("."):
            part = f".{part}"
        exts.add(part)
    return exts


def _slugify_folder(name: str) -> str:
    """Produce a slug suitable for folder_tag values."""
    s = SLUG_RE.sub("-", name.strip().lower())
    return s.strip("-") or "folder"


class FolderSource(Source):
    """Recursively walk a local folder and yield one RawItem per supported file.

    The ``url`` field is the absolute path to the root folder.
    """

    @property
    def source_type(self) -> str:
        return "folder"

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        root = Path(self.url).expanduser().resolve()
        if not root.is_dir():
            log.warning("FolderSource %s: path %s is not a directory", self.name, root)
            return

        include_exts = _parse_ext_list(
            self.options.get("include_extensions", _DEFAULT_INCLUDE)
        )
        skip_exts = _parse_ext_list(
            self.options.get("skip_extensions", _DEFAULT_SKIP)
        )
        max_depth: int = int(self.options.get("max_depth", _DEFAULT_MAX_DEPTH))
        include_fns = self.options.get("include_filenames")
        if isinstance(include_fns, list):
            self._include_filenames = {fn.lower() for fn in include_fns}
        else:
            self._include_filenames = None
        exclude_paths = self.options.get("exclude_paths")
        if isinstance(exclude_paths, list):
            self._exclude_paths = {p.lower() for p in exclude_paths}
        else:
            self._exclude_paths = set()

        yield from self._walk(root, root, since, include_exts, skip_exts, max_depth, 0)

    def _walk(
        self,
        root: Path,
        current: Path,
        since: datetime | None,
        include_exts: set[str],
        skip_exts: set[str],
        max_depth: int,
        depth: int,
    ) -> Iterable[RawItem]:
        if depth > max_depth:
            return

        try:
            entries = sorted(current.iterdir())
        except PermissionError:
            log.debug("FolderSource: permission denied on %s", current)
            return

        for entry in entries:
            # Skip hidden files/dirs.
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                if entry.name.lower() in self._exclude_paths:
                    continue
                yield from self._walk(
                    root, entry, since, include_exts, skip_exts, max_depth, depth + 1
                )
                continue

            if not entry.is_file():
                continue

            suffix = entry.suffix.lower()

            # Filename whitelist filter (if configured).
            if self._include_filenames and entry.name.lower() not in self._include_filenames:
                continue

            # Explicitly skipped extensions produce no output at all.
            if suffix in skip_exts and suffix not in _BINARY_STUB_EXTS:
                continue

            # Binary stubs for media files.
            if suffix in _BINARY_STUB_EXTS:
                item = self._binary_stub(root, entry)
                if item is not None:
                    if since and item.timestamp <= since:
                        continue
                    yield item
                continue

            # Only include files whose extension is in the include list.
            if suffix not in include_exts:
                continue

            try:
                stat = entry.stat()
            except OSError:
                continue

            mtime = datetime.fromtimestamp(stat.st_mtime)
            if since and mtime <= since:
                continue

            body = self._extract(entry, suffix)
            if body is None:
                continue

            rel = entry.relative_to(root)
            title = self._build_title(rel)
            mtime_iso = mtime.isoformat()

            extra: dict[str, Any] = {
                "filename": entry.name,
                "file_path": str(entry),
                "file_size": stat.st_size,
                "relative_folder": str(rel.parent) if rel.parent != Path(".") else "",
            }
            self._apply_option_extras(extra, rel)

            yield RawItem(
                id=self.hash_id(self.name, str(rel), mtime_iso),
                title=title,
                url=str(entry),
                timestamp=mtime,
                body=body,
                extra_frontmatter=extra,
            )

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract(self, f: Path, suffix: str) -> str | None:
        """Return body text for a file, or None to skip it."""
        if suffix in _TEXT_EXTS:
            return self._read_text(f)
        if suffix == ".pdf":
            return self._extract_pdf(f)
        if suffix == ".docx":
            return self._extract_docx(f)
        if suffix == ".doc":
            return self._extract_doc(f)
        if suffix == ".xlsx":
            return self._extract_xlsx(f)
        if suffix == ".csv":
            return self._extract_csv(f)
        # Unknown extension — skip silently.
        return None

    @staticmethod
    def _read_text(f: Path) -> str:
        try:
            return f.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*Text read failed: {e}*"

    @staticmethod
    def _extract_pdf(f: Path) -> str:
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return (
                f"# {f.stem}\n\n"
                "*(PDF body extraction unavailable; install `pdftotext` via `brew install poppler`)*"
            )
        try:
            out = subprocess.run(
                [pdftotext, "-layout", str(f), "-"],
                capture_output=True,
                timeout=60,
                text=True,
            )
            return out.stdout
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*PDF extraction failed: {e}*"

    @staticmethod
    def _extract_docx(f: Path) -> str:
        try:
            import docx  # type: ignore[import-untyped]
        except ImportError:
            return (
                f"# {f.stem}\n\n"
                "*(DOCX extraction unavailable; install python-docx: `uv add python-docx`)*"
            )
        try:
            doc = docx.Document(str(f))
            return "\n\n".join(p.text for p in doc.paragraphs)
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*DOCX extraction failed: {e}*"

    @staticmethod
    def _extract_doc(f: Path) -> str:
        textutil = shutil.which("textutil")
        if not textutil:
            return (
                f"# {f.stem}\n\n"
                "*(DOC extraction unavailable; textutil not found — expected on macOS)*"
            )
        try:
            out = subprocess.run(
                [textutil, "-convert", "txt", "-stdout", str(f)],
                capture_output=True,
                timeout=60,
                text=True,
            )
            return out.stdout
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*DOC extraction failed: {e}*"

    @staticmethod
    def _extract_xlsx(f: Path) -> str:
        try:
            import openpyxl  # type: ignore[import-untyped]
        except ImportError:
            return (
                f"# {f.stem}\n\n"
                "*(XLSX extraction unavailable; install openpyxl: `uv add openpyxl`)*"
            )
        try:
            wb = openpyxl.load_workbook(str(f), read_only=True, data_only=True)
            parts: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                parts.append(f"## {sheet_name}\n")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    parts.append(" | ".join(cells))
                parts.append("")  # blank line between sheets
            wb.close()
            return "\n".join(parts)
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*XLSX extraction failed: {e}*"

    @staticmethod
    def _extract_csv(f: Path) -> str:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            lines: list[str] = []
            for row in reader:
                lines.append(" | ".join(row))
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"# {f.stem}\n\n*CSV extraction failed: {e}*"

    # ------------------------------------------------------------------
    # Binary stub (images / videos)
    # ------------------------------------------------------------------

    def _binary_stub(self, root: Path, f: Path) -> RawItem | None:
        try:
            stat = f.stat()
        except OSError:
            return None

        mtime = datetime.fromtimestamp(stat.st_mtime)
        rel = f.relative_to(root)
        title = self._build_title(rel)
        mtime_iso = mtime.isoformat()

        body = f"# {f.name}\n\n*(Binary file — pending multimodal model support)*"

        extra: dict[str, Any] = {
            "filename": f.name,
            "file_path": str(f),
            "file_size": stat.st_size,
            "relative_folder": str(rel.parent) if rel.parent != Path(".") else "",
            "multimodal_pending": True,
        }
        self._apply_option_extras(extra, rel)

        return RawItem(
            id=self.hash_id(self.name, str(rel), mtime_iso),
            title=title,
            url=str(f),
            timestamp=mtime,
            body=body,
            extra_frontmatter=extra,
        )

    # ------------------------------------------------------------------
    # Title / option helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_title(rel: Path) -> str:
        """Build a human-readable title from a relative path.

        E.g. ``Tax Returns 2024/w2_form.pdf`` -> ``"Tax Returns 2024 / W2 Form"``
        """
        parts: list[str] = []
        for parent in rel.parent.parts:
            parts.append(parent.replace("_", " ").replace("-", " ").strip().title())
        stem = rel.stem.replace("_", " ").replace("-", " ").strip().title()
        parts.append(stem)
        return " / ".join(parts) if len(parts) > 1 else stem

    def _apply_option_extras(self, extra: dict[str, Any], rel: Path) -> None:
        """Add option-driven extra_frontmatter fields."""
        visibility = self.options.get("visibility")
        if visibility:
            extra["visibility"] = visibility

        if self.options.get("subfolder_as_tag") and rel.parent != Path("."):
            top_folder = rel.parts[0]
            extra["folder_tag"] = f"personal/{_slugify_folder(top_folder)}"
