"""Claude Code conversation history connector for Muninn.

Ingests conversations from ~/.claude/projects/*/*.jsonl — one RawItem per session.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .base import RawItem, Source

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 8_000


class ClaudeHistorySource(Source):
    """Read Claude Code JSONL conversation logs and yield them as RawItems."""

    @property
    def source_type(self) -> str:
        return "claude-history"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        base = Path(self.url).expanduser()
        projects_dir = base / "projects"
        if not projects_dir.exists():
            log.warning("Claude projects dir not found: %s", projects_dir)
            return

        max_conversations: int = self.options.get("max_conversations", 20)
        min_messages: int = self.options.get("min_messages", 3)
        exclude_projects: list[str] = self.options.get("exclude_projects", [])

        # Collect all .jsonl files with their mtimes, sorted newest-first.
        candidates: list[tuple[Path, datetime]] = []
        for jsonl_path in projects_dir.rglob("*.jsonl"):
            mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
            if since and mtime <= since:
                continue

            # Check project exclusions.
            rel = str(jsonl_path.relative_to(projects_dir))
            if any(exc in rel for exc in exclude_projects):
                continue

            candidates.append((jsonl_path, mtime))

        # Sort newest first and cap.
        candidates.sort(key=lambda c: c[1], reverse=True)
        candidates = candidates[:max_conversations]

        for jsonl_path, mtime in candidates:
            try:
                item = self._parse_conversation(jsonl_path, mtime, min_messages)
            except Exception:  # noqa: BLE001
                log.exception("Failed to parse %s", jsonl_path)
                continue
            if item is not None:
                yield item

    # ------------------------------------------------------------------

    def _parse_conversation(
        self, path: Path, mtime: datetime, min_messages: int
    ) -> RawItem | None:
        """Parse a single JSONL conversation file into a RawItem."""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        messages: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in ("user", "assistant"):
                messages.append(obj)

        if len(messages) < min_messages:
            return None

        # Build conversation body.
        parts: list[str] = []
        first_user_text = ""
        message_count = 0
        for msg in messages:
            role = msg["type"]
            text_parts = self._extract_text_parts(msg)
            if not text_parts:
                continue
            message_count += 1
            combined = "\n".join(text_parts)
            label = "User" if role == "user" else "Assistant"
            parts.append(f"**{label}:** {combined}")

            if role == "user" and not first_user_text:
                first_user_text = combined

        if not parts:
            return None

        body = "\n\n".join(parts)
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n*(truncated)*"

        # Derive project name from path.
        # e.g. projects/-Users-john-git-MyProject/abc123.jsonl
        project_dir_name = path.parent.name
        project_name = self._project_name_from_path(project_dir_name)
        session_id = path.stem

        title_suffix = (first_user_text[:60].strip() or session_id)
        title = f"Claude: {project_name} — {title_suffix}"

        return RawItem(
            id=self.hash_id(self.name, session_id),
            title=title,
            url=str(path),
            timestamp=mtime,
            body=body,
            extra_frontmatter={
                "session_id": session_id,
                "project": project_name,
                "message_count": message_count,
            },
        )

    @staticmethod
    def _extract_text_parts(msg: dict[str, Any]) -> list[str]:
        """Pull plain-text content from a user or assistant message."""
        content = msg.get("message", {}).get("content")
        if content is None:
            return []
        # content can be a plain string or a list of parts.
        if isinstance(content, str):
            return [content] if content.strip() else []
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text", "").strip()
                if t:
                    texts.append(t)
            elif isinstance(part, str):
                if part.strip():
                    texts.append(part.strip())
        return texts

    @staticmethod
    def _project_name_from_path(dir_name: str) -> str:
        """Derive a readable project name from the directory name.

        Claude uses URL-encoded or dash-separated absolute paths, e.g.
        ``-Users-john-git-MyProject``. We take the last meaningful
        segment as the project name.
        """
        # Strip leading/trailing dashes and split.
        segments = [s for s in dir_name.strip("-").split("-") if s]
        if not segments:
            return dir_name
        # Return the last segment (typically the repo/project name).
        return segments[-1]
