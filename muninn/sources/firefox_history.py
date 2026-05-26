"""Firefox browser history connector for Muninn.

Firefox stores history in `places.sqlite` inside the user profile directory.
Tables: `moz_places` (URLs) + `moz_historyvisits` (visit timestamps).
Timestamps are microseconds since Unix epoch.

Typical paths:
  macOS:  ~/Library/Application Support/Firefox/Profiles/*.default-release/places.sqlite
  Linux:  ~/.mozilla/firefox/*.default-release/places.sqlite
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import httpx
import trafilatura

from .base import RawItem, Source
from .browser_history import _hostname, _strip_tracking


class FirefoxHistorySource(Source):
    """Read browser history from Firefox's places.sqlite."""

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
        if not db_path or not db_path.exists():
            return
        exclude = set(self.options.get("exclude_domains", [])) | self.DEFAULT_EXCLUDE_DOMAINS
        min_visits = int(self.options.get("min_visit_count", 1))
        max_items = int(self.options.get("max_items", 50))
        fetch_body = bool(self.options.get("fetch_body", True))
        since_us = int(since.timestamp() * 1_000_000) if since else 0

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "places.sqlite"
            try:
                shutil.copy2(db_path, tmp)
            except (PermissionError, FileNotFoundError):
                return
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    """
                    SELECT p.id, p.url, p.title, p.visit_count,
                           MAX(v.visit_date) as last_visit
                    FROM moz_places p
                    JOIN moz_historyvisits v ON v.place_id = p.id
                    WHERE v.visit_date > ?
                      AND p.visit_count >= ?
                      AND p.url LIKE 'http%'
                    GROUP BY p.id
                    ORDER BY last_visit DESC
                    LIMIT ?
                    """,
                    (since_us, min_visits, max_items),
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
            for idx, (_, raw_url, title, visits, ts_us) in enumerate(rows, 1):
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

                visited = datetime.fromtimestamp(ts_us / 1_000_000)
                body = ""
                if fetch_body:
                    try:
                        cols = os.get_terminal_size().columns
                    except OSError:
                        cols = 120
                    line = f"  [{idx}/{total}] fetching {host}/{url.split('/')[-1][:40]}"
                    sys.stderr.write(f"\r{line:<{cols}}")
                    sys.stderr.flush()
                    body = self._fetch_body(url, client=http_client)
                    if not body and visits < int(self.options.get("min_visit_count_no_body", 2)):
                        continue

                yielded += 1
                yield RawItem(
                    id=self.hash_id(self.name, url),
                    title=(title or "").strip() or url,
                    url=url,
                    timestamp=visited,
                    body=body or f"# {title or url}\n\nNo body extracted.",
                    extra_frontmatter={
                        "browser": "firefox",
                        "visit_count": int(visits),
                        "domain": host,
                        "defuddled": bool(body),
                        "last_visit": visited.isoformat(),
                    },
                )
        finally:
            if http_client:
                http_client.close()

    def _resolve_db_path(self) -> Path | None:
        url = self.url
        if url.startswith("file://"):
            url = url[len("file://"):]
        path = Path(url).expanduser()

        # Direct path to places.sqlite
        if path.name == "places.sqlite" and path.exists():
            return path

        # Profile directory — find places.sqlite inside
        if path.is_dir():
            db = path / "places.sqlite"
            if db.exists():
                return db

        # Firefox root — find the default profile
        profiles_dir = path / "Profiles" if (path / "Profiles").exists() else path
        for profile in sorted(profiles_dir.iterdir(), reverse=True):
            db = profile / "places.sqlite"
            if db.exists():
                return db
        return None

    @staticmethod
    def _fetch_body(url: str, client: httpx.Client | None = None) -> str:
        try:
            Source.validate_fetch_url(url)
            c = client or httpx.Client(timeout=15.0, follow_redirects=True)
            r = c.get(url)
            if not client:
                c.close()
            if r.status_code != 200 or "text/html" not in (r.headers.get("content-type") or ""):
                return ""
            if len(r.history) >= 3:
                return ""
            extracted = trafilatura.extract(
                r.text,
                include_comments=False,
                include_tables=True,
                favor_recall=False,
                output_format="markdown",
                with_metadata=False,
            )
            return (extracted or "").strip()
        except Exception:  # noqa: BLE001
            return ""
