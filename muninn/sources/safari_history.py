"""Safari browser history connector for Muninn.

Safari stores history in `History.db` with tables `history_items` + `history_visits`.
Timestamps are seconds since 2001-01-01 (Core Data / NSDate epoch).

Path: ~/Library/Safari/History.db

Note: macOS requires Full Disk Access for the process reading Safari's history.
System Preferences → Privacy & Security → Full Disk Access → add Terminal / iTerm.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import httpx
import trafilatura

from .base import RawItem, Source
from .browser_history import _hostname, _strip_tracking

SAFARI_EPOCH = datetime(2001, 1, 1)


class SafariHistorySource(Source):
    """Read browser history from Safari's History.db."""

    DEFAULT_EXCLUDE_DOMAINS = {
        "google.com", "www.google.com", "accounts.google.com",
        "duckduckgo.com", "bing.com",
        "localhost", "127.0.0.1",
        "mail.google.com", "calendar.google.com",
    }

    @property
    def source_type(self) -> str:
        return "browser-history"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        db_path = self._resolve_db_path()
        if not db_path.exists():
            return
        exclude = set(self.options.get("exclude_domains", [])) | self.DEFAULT_EXCLUDE_DOMAINS
        min_visits = int(self.options.get("min_visit_count", 1))
        max_items = int(self.options.get("max_items", 50))
        fetch_body = bool(self.options.get("fetch_body", True))
        since_safari = (since - SAFARI_EPOCH).total_seconds() if since else 0

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "History.db"
            try:
                shutil.copy2(db_path, tmp)
            except (PermissionError, FileNotFoundError) as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Cannot read Safari history: %s. Grant Full Disk Access to your terminal.", e)
                return
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    """
                    SELECT i.id, i.url, i.visit_count,
                           MAX(v.visit_time) as last_visit
                    FROM history_items i
                    JOIN history_visits v ON v.history_item = i.id
                    WHERE v.visit_time > ?
                      AND i.visit_count >= ?
                      AND i.url LIKE 'http%'
                    GROUP BY i.id
                    ORDER BY last_visit DESC
                    LIMIT ?
                    """,
                    (since_safari, min_visits, max_items),
                ).fetchall()
            finally:
                conn.close()

        seen: set[str] = set()
        total = len(rows)
        yielded = 0
        http_client = httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Muninn/0.1.0"},
        ) if fetch_body else None

        try:
            for idx, (_, raw_url, visits, ts) in enumerate(rows, 1):
                url = _strip_tracking(raw_url)
                if not url or url in seen:
                    continue
                seen.add(url)
                host = _hostname(url)
                if any(host == d or host.endswith("." + d) for d in exclude):
                    continue
                url_lower = url.lower()
                if any(seg in url_lower for seg in (
                    "/login", "/signin", "/authorize", "/oauth",
                    "/sso/", "/callback", "/logout", "/signup",
                )):
                    continue

                visited = SAFARI_EPOCH + timedelta(seconds=ts)
                title = ""
                body = ""
                if fetch_body:
                    try:
                        cols = os.get_terminal_size().columns
                    except OSError:
                        cols = 120
                    line = f"  [{idx}/{total}] fetching {host}/{url.split('/')[-1][:40]}"
                    sys.stderr.write(f"\r{line:<{cols}}")
                    sys.stderr.flush()
                    body, title = self._fetch_body_and_title(url, client=http_client)
                    if not body and visits < int(self.options.get("min_visit_count_no_body", 2)):
                        continue

                yielded += 1
                yield RawItem(
                    id=self.hash_id(self.name, url),
                    title=title or url,
                    url=url,
                    timestamp=visited,
                    body=body or f"# {title or url}\n\nNo body extracted.",
                    extra_frontmatter={
                        "browser": "safari",
                        "visit_count": int(visits),
                        "domain": host,
                        "defuddled": bool(body),
                        "last_visit": visited.isoformat(),
                    },
                )
        finally:
            if http_client:
                http_client.close()

    def _resolve_db_path(self) -> Path:
        url = self.url
        if url.startswith("file://"):
            url = url[len("file://"):]
        return Path(url).expanduser()

    @staticmethod
    def _fetch_body_and_title(url: str, client: httpx.Client | None = None) -> tuple[str, str]:
        try:
            Source.validate_fetch_url(url)
            c = client or httpx.Client(timeout=15.0, follow_redirects=True)
            r = c.get(url)
            if not client:
                c.close()
            if r.status_code != 200 or "text/html" not in (r.headers.get("content-type") or ""):
                return "", ""
            if len(r.history) >= 3:
                return "", ""
            # Extract title from HTML
            title = ""
            import re
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.IGNORECASE | re.DOTALL)
            if m:
                title = m.group(1).strip()[:200]
            body = trafilatura.extract(
                r.text,
                include_comments=False,
                include_tables=True,
                favor_recall=False,
                output_format="markdown",
                with_metadata=False,
            )
            return (body or "").strip(), title
        except Exception:  # noqa: BLE001
            return "", ""
