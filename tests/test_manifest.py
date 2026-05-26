"""Manifest round-trip + state transitions."""

from __future__ import annotations

from datetime import datetime

from muninn.manifest import Manifest


def test_cursor_round_trip(tmp_path):
    mf = Manifest(tmp_path / "manifest.json")
    assert mf.cursor("src") is None
    now = datetime(2026, 5, 22, 13, 0, 0)
    mf.set_cursor("src", now)
    assert mf.cursor("src") == now


def test_item_lifecycle(tmp_path):
    mf = Manifest(tmp_path / "manifest.json")
    mf.add_item("src", "id1", "Raw/Sources/src/2026-05-22/id1.md")
    assert mf.has_item("src", "id1")

    pending = list(mf.pending("src"))
    assert len(pending) == 1
    assert pending[0].item_id == "id1"

    mf.mark_processed("src", "id1", ["[[Page]]"])
    assert not list(mf.pending("src"))
    item = mf.get_item("src", "id1")
    assert item is not None
    assert item.status == "processed"
    assert item.pages_touched == ["[[Page]]"]


def test_summary_counts(tmp_path):
    mf = Manifest(tmp_path / "manifest.json")
    mf.add_item("src", "a", "path/a.md")
    mf.add_item("src", "b", "path/b.md")
    mf.add_item("src", "c", "path/c.md")
    mf.mark_processed("src", "a", [])
    mf.mark_failed("src", "b", "boom")
    s = mf.summary("src")
    assert s.processed == 1
    assert s.failed == 1
    assert s.pending == 1
    assert s.skipped == 0


def test_reset_source(tmp_path):
    mf = Manifest(tmp_path / "manifest.json")
    mf.set_cursor("src", datetime(2026, 5, 22))
    mf.add_item("src", "a", "x")
    mf.reset_source("src")
    assert mf.cursor("src") is None
    assert mf.summary("src").processed == 0
