"""YouTube transcript connector for Muninn.

Scans existing browser-history raw files for YouTube watch URLs, fetches
transcripts via ``youtube_transcript_api``, and yields them as RawItems.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from .base import RawItem, Source

log = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")


class YouTubeTranscriptSource(Source):
    """Enrich browser-history raws that point to YouTube with full transcripts."""

    @property
    def source_type(self) -> str:
        return "youtube"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-untyped]
        except ImportError:
            log.error(
                "youtube_transcript_api is not installed. "
                "Install it with: pip install youtube-transcript-api"
            )
            return

        vault = Path(self.url).expanduser()
        max_videos: int = self.options.get("max_videos", 20)
        languages: list[str] = self.options.get("languages", ["en"])
        raw_sources: list[str] = self.options.get(
            "raw_sources", ["edge-history", "chrome-history"]
        )

        youtube_files = self._find_youtube_raws(vault, raw_sources, since)
        yielded = 0

        for _raw_path, frontmatter in youtube_files:
            if yielded >= max_videos:
                break

            source_url: str = frontmatter.get("source_url", "")
            video_id = self._extract_video_id(source_url)
            if not video_id:
                continue

            try:
                transcript = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=languages
                )
            except Exception:  # noqa: BLE001
                log.debug("No transcript for %s (%s)", video_id, source_url)
                continue

            body = self._transcript_to_text(transcript)
            if not body:
                continue

            raw_title = frontmatter.get("title", video_id)
            title = f"YouTube: {raw_title}"

            yield RawItem(
                id=self.hash_id(self.name, video_id),
                title=title,
                url=source_url,
                timestamp=datetime.now(),
                body=body,
                extra_frontmatter={
                    "video_id": video_id,
                    "source_url": source_url,
                    "transcript_length": len(body),
                },
            )
            yielded += 1

    # ------------------------------------------------------------------

    def _find_youtube_raws(
        self,
        vault: Path,
        raw_sources: list[str],
        since: datetime | None,
    ) -> list[tuple[Path, dict[str, Any]]]:
        """Return (path, frontmatter) pairs for raw files with YouTube URLs."""
        results: list[tuple[Path, dict[str, Any]]] = []

        for source_name in raw_sources:
            source_dir = vault / "Raw" / "Sources" / source_name
            if not source_dir.exists():
                continue

            for md_path in source_dir.glob("*.md"):
                if since:
                    mtime = datetime.fromtimestamp(md_path.stat().st_mtime)
                    if mtime <= since:
                        continue

                fm = self._read_frontmatter(md_path)
                if not fm:
                    continue

                source_url = fm.get("source_url", "")
                if "youtube.com/watch" in source_url or "youtu.be/" in source_url:
                    results.append((md_path, fm))

        return results

    @staticmethod
    def _read_frontmatter(path: Path) -> dict[str, Any] | None:
        """Read YAML frontmatter from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end == -1:
            return None
        try:
            return yaml.safe_load(text[3:end]) or {}
        except yaml.YAMLError:
            return None

    @staticmethod
    def _extract_video_id(url: str) -> str | None:
        """Pull the 11-char video ID from a YouTube URL."""
        parsed = urlparse(url)
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            vid = qs.get("v", [None])[0]
            if vid and VIDEO_ID_RE.match(vid):
                return vid
        elif "youtu.be" in parsed.netloc:
            vid = parsed.path.lstrip("/").split("/")[0]
            if vid and VIDEO_ID_RE.match(vid):
                return vid
        return None

    @staticmethod
    def _transcript_to_text(transcript: list[dict[str, Any]]) -> str:
        """Join transcript segments into plain text."""
        lines: list[str] = []
        for seg in transcript:
            text = seg.get("text", "").strip()
            if text:
                lines.append(text)
        return " ".join(lines)
