"""Single-URL connector — explicit `muninn ingest-url <URL>` path."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from datetime import datetime

import httpx
import trafilatura

from .base import RawItem, Source


class UrlSource(Source):
    """Used internally for `ingest-url`. Doesn't appear in wiki.yaml normally.

    The `url` constructor field is unused — actual URLs are passed via .ingest_one().
    """

    @property
    def source_type(self) -> str:
        return "url"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        # The URL source doesn't auto-fetch; URLs come in via ingest_one().
        return []

    def ingest_one(self, url: str) -> RawItem | None:
        self.validate_fetch_url(url)
        try:
            r = httpx.get(
                url,
                follow_redirects=True,
                timeout=20.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Muninn/0.1.0"
                    )
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"fetch failed: {e}") from e

        if "text/html" not in (r.headers.get("content-type") or ""):
            body = r.text
            title = url
        else:
            extracted = trafilatura.extract(
                r.text,
                include_comments=False,
                include_tables=True,
                output_format="markdown",
                with_metadata=True,
                favor_recall=False,
            )
            body = (extracted or "").strip()
            md = trafilatura.metadata.extract_metadata(r.text)
            title = (md.title if md else None) or url

        parsed = urllib.parse.urlparse(url)
        domain = parsed.hostname or ""
        return RawItem(
            id=self.hash_id(self.name, url),
            title=title,
            url=url,
            timestamp=datetime.now(),
            body=body or f"# {title}\n\nNo body extracted.",
            extra_frontmatter={
                "domain": domain,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "word_count": len(body.split()),
                "defuddled": bool(body),
            },
        )
