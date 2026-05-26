"""Browser history connector — reads sqlite, applies filters, emits RawItems."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from muninn.sources.browser_history import (
    CHROME_EPOCH,
    BrowserHistorySource,
    chrome_ts_to_dt,
    dt_to_chrome_ts,
)


def test_chrome_ts_round_trip():
    when = datetime(2026, 5, 22, 14, 30, 0)
    us = dt_to_chrome_ts(when)
    back = chrome_ts_to_dt(us)
    assert back == when


def _make_history_db(tmp_path: Path) -> Path:
    db = tmp_path / "History"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE urls (
            id INTEGER PRIMARY KEY,
            url TEXT,
            title TEXT,
            visit_count INTEGER,
            last_visit_time INTEGER
        )
        """
    )
    now = datetime.now()
    rows = [
        (1, "https://karpathy.ai/llm-wiki", "LLM Wiki", 3, dt_to_chrome_ts(now)),
        (2, "https://google.com/search?q=foo", "search", 7, dt_to_chrome_ts(now - timedelta(hours=1))),
        (3, "https://example.com/article", "Article", 2, dt_to_chrome_ts(now - timedelta(hours=2))),
        (4, "https://example.com/old", "Old", 5, dt_to_chrome_ts(now - timedelta(days=5))),
    ]
    conn.executemany("INSERT INTO urls VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_filters_exclude_domain_and_returns_recent(tmp_path):
    db = _make_history_db(tmp_path)
    src = BrowserHistorySource(
        name="test-history",
        url=str(db),
        secret=None,
        options={
            "browser": "chrome",
            "min_visit_count": 1,
            "fetch_body": False,  # avoid network in tests
            "max_items": 50,
            "exclude_domains": ["google.com"],
        },
    )
    items = list(src.fetch(since=None))
    urls = {i.url for i in items}
    # The Google search must have been filtered out by the exclude list.
    assert not any("google.com" in u for u in urls)
    # The Karpathy + example.com items should be present.
    assert any("karpathy.ai" in u for u in urls)
    assert any("example.com/article" in u for u in urls)


def test_since_cursor_filters_old_visits(tmp_path):
    db = _make_history_db(tmp_path)
    src = BrowserHistorySource(
        name="test-history",
        url=str(db),
        secret=None,
        options={"browser": "chrome", "min_visit_count": 1, "fetch_body": False, "exclude_domains": []},
    )
    items = list(src.fetch(since=datetime.now() - timedelta(hours=4)))
    urls = {i.url for i in items}
    # the 5-day-old item should be excluded
    assert not any("example.com/old" in u for u in urls)
