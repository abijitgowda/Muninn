"""Chrome / Edge / Arc / Brave / Vivaldi browser history connector for Muninn.

Chromium-based browsers store history in a SQLite DB at predictable paths. We open
the file in read-only mode (mode=ro URI) so we don't block the browser writing to it,
and we still need to make a copy because Chrome holds the file with EXCLUSIVE in macOS.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import httpx
import trafilatura

from .base import RawItem, Source


# Chromium stores timestamps as microseconds since 1601-01-01.
CHROME_EPOCH = datetime(1601, 1, 1)


def chrome_ts_to_dt(us: int) -> datetime:
    return CHROME_EPOCH + timedelta(microseconds=us)


def dt_to_chrome_ts(dt: datetime) -> int:
    return int((dt - CHROME_EPOCH).total_seconds() * 1_000_000)


def _hostname(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def _strip_tracking(url: str) -> str:
    try:
        parts = urllib.parse.urlparse(url)
        q = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parts.query)
            if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref"))
        ]
        clean = parts._replace(query=urllib.parse.urlencode(q), fragment="")
        return urllib.parse.urlunparse(clean)
    except Exception:  # noqa: BLE001
        return url


class BrowserHistorySource(Source):
    """Read browser history from a Chromium-style sqlite DB."""

    DEFAULT_EXCLUDE_DOMAINS = {
        "google.com",
        "www.google.com",
        "accounts.google.com",
        "duckduckgo.com",
        "bing.com",
        "localhost",
        "127.0.0.1",
        "mail.google.com",
        "calendar.google.com",
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
        since_us = dt_to_chrome_ts(since) if since else 0

        # Chromium locks the file; copy first.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "History"
            try:
                shutil.copy2(db_path, tmp)
            except (PermissionError, FileNotFoundError):
                return
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    """
                    SELECT id, url, title, visit_count, last_visit_time
                    FROM urls
                    WHERE last_visit_time > ?
                      AND visit_count >= ?
                    ORDER BY last_visit_time DESC
                    LIMIT ?
                    """,
                    (since_us, min_visits, max_items),
                ).fetchall()
            finally:
                conn.close()

        seen_canonical: set[str] = set()
        total = len(rows)
        yielded = 0
        http_client = httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Muninn/0.1.0"
                )
            },
        ) if fetch_body else None
        try:
            for idx, (_, raw_url, title, visits, ts) in enumerate(rows, 1):
                url = _strip_tracking(raw_url)
                if not url or url in seen_canonical:
                    continue
                seen_canonical.add(url)
                host = _hostname(url)
                if any(host == d or host.endswith("." + d) for d in exclude):
                    continue
                # Skip URLs that never produce useful content (auth, login, OAuth)
                url_lower = url.lower()
                if any(seg in url_lower for seg in (
                    "/login", "/signin", "/sign-in", "/authorize", "/oauth",
                    "/sso/", "/saml/", "/callback", "/logout", "/signup",
                    "client_id=", "redirect_uri=", "response_type=",
                )):
                    continue
                visited = chrome_ts_to_dt(ts)
                body = ""
                if fetch_body:
                    try:
                        cols = os.get_terminal_size().columns
                    except OSError:
                        cols = 120
                    line = f"  [{idx}/{total}] fetching {host}/{url.split('/')[-1][:40]}"
                    sys.stderr.write(f"\r{line:<{cols}}")
                    sys.stderr.flush()
                    body = self._fetch_clean_body(url, client=http_client)
                    if not body and visits < int(self.options.get("min_visit_count_no_body", 2)):
                        continue
                yielded += 1
                if fetch_body and yielded % 10 == 0:
                    try:
                        cols = os.get_terminal_size().columns
                    except OSError:
                        cols = 120
                    line = f"  [{idx}/{total}] yielded {yielded} items so far"
                    sys.stderr.write(f"\r{line:<{cols}}\n")
                    sys.stderr.flush()
                yield RawItem(
                    id=self.hash_id(self.name, url),
                    title=(title or "").strip() or url,
                    url=url,
                    timestamp=visited,
                    body=body or f"# {title or url}\n\nNo body extracted.",
                    extra_frontmatter={
                        "browser": self.options.get("browser", "unknown"),
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

    def _fetch_clean_body(self, url: str, client: httpx.Client | None = None) -> str:
        try:
            self.validate_fetch_url(url)
            if client is not None:
                r = client.get(url)
            else:
                c = httpx.Client(timeout=15.0, follow_redirects=True)
                r = c.get(url, headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Muninn/0.1.0"
                    )
                })
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
