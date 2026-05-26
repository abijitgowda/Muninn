"""Inbox connector for Muninn — watches `<vault>/Inbox/` for files the user drops in."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .base import RawItem, Source


class InboxSource(Source):
    """Pick up files in <vault>/Inbox/ and queue them for ingest.

    The `url` field for this source is the absolute path to the inbox folder.
    After successful ingest the original is moved to <vault>/Inbox/.processed/<date>/.
    """

    @property
    def source_type(self) -> str:
        return "inbox"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        inbox = Path(self.url).expanduser()
        if not inbox.exists():
            return
        processed = inbox / ".processed"
        processed.mkdir(exist_ok=True)
        for f in inbox.iterdir():
            if f.name.startswith("."):
                continue
            if f.is_dir():
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if since and mtime <= since:
                continue
            yield self._file_to_item(f, mtime)

    def _file_to_item(self, f: Path, mtime: datetime) -> RawItem:
        title = f.stem.replace("_", " ").replace("-", " ").strip().title() or f.name
        suffix = f.suffix.lower()
        if suffix in (".md", ".markdown", ".txt"):
            body = f.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            body = self._pdf_body(f)
        else:
            body = f"# {title}\n\nUnsupported file type: {suffix}. Path: `{f}`"

        return RawItem(
            id=self.hash_id(self.name, f.name, mtime.isoformat()),
            title=title,
            url=str(f),
            timestamp=mtime,
            body=body,
            extra_frontmatter={
                "filename": f.name,
                "file_path": str(f),
                "file_size": f.stat().st_size,
            },
        )

    def _pdf_body(self, f: Path) -> str:
        # Lightweight: try pdftotext if installed; else note unavailable.
        import shutil
        import subprocess

        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return f"# {f.stem}\n\n*(PDF body extraction unavailable; install `pdftotext` via `brew install poppler`)*"
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
