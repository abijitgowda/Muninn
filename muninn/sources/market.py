"""Financial market news connector for Muninn.

Searches DuckDuckGo for stock-market news for a configurable watchlist of tickers,
fetches each article body with trafilatura, and yields RawItems for the ingest pipeline.

Watchlist comes from the env var named by ``options.watchlist_env`` (default
``MARKET_WATCHLIST``), as a comma-separated list of ticker symbols.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable

import trafilatura

from .base import RawItem, Source

log = logging.getLogger(__name__)

try:
    from ddgs import DDGS

    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS

        _HAS_DDGS = True
    except ImportError:
        _HAS_DDGS = False


class MarketSource(Source):
    """Financial market news connector using DuckDuckGo search.

    Options (set in ``wiki.yaml``)::

        watchlist_env: "MARKET_WATCHLIST"    # env var holding comma-separated tickers
        discover_trending: true             # also search for trending market movers
        max_results_per_ticker: 3           # max articles per ticker
    """

    @property
    def source_type(self) -> str:
        return "market"

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        if not _HAS_DDGS:
            raise RuntimeError(
                f"{self.name}: duckduckgo_search (ddgs) is not installed — "
                "run: pip install ddgs"
            )

        tickers = self._read_watchlist()
        trending_cfg = self.options.get("trending") or {}
        trending_enabled = trending_cfg.get("enabled", self.options.get("discover_trending", False))

        if not tickers and not trending_enabled:
            log.warning("%s: empty watchlist and trending disabled — nothing to fetch", self.name)
            return

        queries: list[tuple[str, str]] = []
        today = datetime.now().strftime("%Y-%m-%d")

        # Static watchlist — multiple targeted queries per ticker.
        for ticker in tickers:
            ticker_queries = [
                f"{ticker} stock price target analyst upgrade downgrade {today}",
                f"{ticker} earnings revenue EPS guidance forecast {today}",
                f"{ticker} breaking news announcement product launch acquisition {today}",
            ]
            for tq in ticker_queries:
                queries.append((ticker, tq))

        # Dynamic trending — configurable discovery queries
        if trending_enabled:
            default_queries = [
                "trending AI stocks today market movers",
                "semiconductor stocks top gainers today",
                "stock market movers premarket today",
            ]
            discovery = trending_cfg.get("queries", default_queries)
            for q in discovery:
                queries.append(("TRENDING", q))

        for ticker_label, query in queries:
            try:
                yield from self._search_and_extract(ticker_label, query)
            except Exception:  # noqa: BLE001
                log.warning(
                    "%s: search failed for %s — skipping",
                    self.name, ticker_label, exc_info=True,
                )

    def _read_watchlist(self) -> list[str]:
        wl = self.options.get("watchlist") or self.options.get("watchlist_env")
        if isinstance(wl, list):
            return [str(t).strip().upper() for t in wl if str(t).strip()]
        if isinstance(wl, str):
            raw = os.environ.get(wl, wl)
            return [t.strip().upper() for t in raw.split(",") if t.strip()]
        return []

    def _search_and_extract(
        self, ticker: str, query: str
    ) -> Iterable[RawItem]:
        max_results = int(self.options.get("max_results_per_ticker", 3))
        results = DDGS().text(query, max_results=max_results + 3)
        yielded = 0

        for result in results:
            url = result.get("href") or result.get("link") or ""
            article_title = result.get("title", "")
            snippet = result.get("body", "")

            if not url:
                continue
            # Skip generic homepages and aggregator landing pages
            if url.rstrip("/").count("/") <= 3 and not any(
                k in url.lower() for k in [ticker.lower(), "stock", "market", "finance", "invest"]
            ):
                continue

            body = self._fetch_article_body(url)
            if not body or len(body) < 200:
                body = snippet or f"# {article_title}\n\nNo body extracted."
            if len(body) < 100:
                continue

            yielded += 1
            yield RawItem(
                id=self.hash_id(self.name, ticker, url),
                title=f"{ticker}: {article_title}",
                url=url,
                timestamp=datetime.now(),
                body=body,
                extra_frontmatter={
                    "ticker": ticker,
                    "search_query": query,
                    "source_url": url,
                },
            )
            if yielded >= max_results:
                break

    @staticmethod
    def _fetch_article_body(url: str) -> str:
        try:
            html = trafilatura.fetch_url(url)
            if not html:
                return ""
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_recall=False,
                output_format="markdown",
                with_metadata=False,
            )
            return (extracted or "").strip()
        except Exception:  # noqa: BLE001
            log.debug("trafilatura extraction failed for %s", url, exc_info=True)
            return ""
