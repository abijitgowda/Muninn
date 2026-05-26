"""Manifest — per-source cursor + per-item processing state.

SQLite-backed store at `<vault>/.muninn/manifest.db`.
WAL mode for concurrent read/write safety.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SourceSummary:
    name: str
    cursor: str | None
    pending: int
    processed: int
    failed: int
    skipped: int
    avg_tok_s: float = 0.0
    avg_duration_s: float = 0.0
    total_items_with_metrics: int = 0


@dataclass
class Item:
    item_id: str
    path: str
    status: str
    first_seen: str
    last_update: str
    pages_touched: list[str]
    error: str | None
    source_name: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    cursor TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id TEXT NOT NULL,
    source TEXT NOT NULL,
    path TEXT,
    status TEXT DEFAULT 'pending',
    first_seen TEXT,
    last_update TEXT,
    pages_touched TEXT DEFAULT '[]',
    error TEXT,
    eval_rate REAL,
    duration_ms REAL,
    PRIMARY KEY (source, id)
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(source, status);
CREATE TABLE IF NOT EXISTS content_hashes (
    hash TEXT NOT NULL,
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (hash)
);
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT,
    cited_pages TEXT DEFAULT '[]',
    eval_rate REAL,
    duration_ms REAL,
    retrieval_mode TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS relationships (
    source_page TEXT NOT NULL,
    target_page TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence TEXT DEFAULT 'medium',
    PRIMARY KEY (source_page, target_page, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_page);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_page);
"""


class Manifest:
    """SQLite-backed manifest store. Thread-safe via WAL mode."""

    def __init__(self, path: Path) -> None:
        self.path = path if path.suffix == ".db" else path.with_suffix(".db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._batch_depth = 0

    def batch(self):
        """Context manager that defers commits until the batch exits."""
        return _BatchContext(self)

    def _commit(self) -> None:
        if self._batch_depth == 0:
            self._conn.commit()

    # ---- public API (same interface as before) ----

    def cursor(self, source_name: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT cursor FROM sources WHERE name = ?", (source_name,)
        ).fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])

    def set_cursor(self, source_name: str, when: datetime) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sources (name, cursor) VALUES (?, ?)",
            (source_name, when.isoformat()),
        )
        self._commit()

    def has_item(self, source_name: str, item_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM items WHERE source = ? AND id = ?",
            (source_name, item_id),
        ).fetchone()
        return row is not None

    def add_item(
        self,
        source_name: str,
        item_id: str,
        raw_path: str,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            """INSERT OR IGNORE INTO items
               (id, source, path, status, first_seen, last_update, pages_touched, error)
               VALUES (?, ?, ?, 'pending', ?, ?, '[]', NULL)""",
            (item_id, source_name, raw_path, now, now),
        )
        # Ensure source row exists
        self._conn.execute(
            "INSERT OR IGNORE INTO sources (name, cursor) VALUES (?, NULL)",
            (source_name,),
        )
        self._commit()

    def update_item(
        self,
        source_name: str,
        item_id: str,
        *,
        status: str | None = None,
        pages_touched: list[str] | None = None,
        error: str | None = None,
        eval_rate: float | None = None,
        total_duration_ms: float | None = None,
    ) -> None:
        sets: list[str] = []
        vals: list = []
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        if pages_touched is not None:
            sets.append("pages_touched = ?")
            vals.append(json.dumps(pages_touched))
        if error is not None:
            sets.append("error = ?")
            vals.append(error)
        if eval_rate is not None:
            sets.append("eval_rate = ?")
            vals.append(round(eval_rate, 1))
        if total_duration_ms is not None:
            sets.append("duration_ms = ?")
            vals.append(round(total_duration_ms))
        sets.append("last_update = ?")
        vals.append(datetime.now().isoformat(timespec="seconds"))
        vals.extend([source_name, item_id])
        self._conn.execute(
            f"UPDATE items SET {', '.join(sets)} WHERE source = ? AND id = ?",
            vals,
        )
        self._commit()

    def mark_processed(
        self,
        source_name: str,
        item_id: str,
        pages_touched: list[str],
        eval_rate: float | None = None,
        total_duration_ms: float | None = None,
    ) -> None:
        self.update_item(
            source_name, item_id,
            status="processed", pages_touched=pages_touched, error=None,
            eval_rate=eval_rate, total_duration_ms=total_duration_ms,
        )

    def mark_failed(self, source_name: str, item_id: str, error: str) -> None:
        self.update_item(source_name, item_id, status="failed", error=error)

    def pending(self, source_name: str | None = None) -> Iterator[Item]:
        if source_name:
            rows = self._conn.execute(
                "SELECT id, source, path, status, first_seen, last_update, pages_touched, error "
                "FROM items WHERE source = ? AND status = 'pending'",
                (source_name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, source, path, status, first_seen, last_update, pages_touched, error "
                "FROM items WHERE status = 'pending'"
            ).fetchall()
        for row in rows:
            yield Item(
                item_id=row[0],
                path=row[2] or "",
                status=row[3],
                first_seen=row[4] or "",
                last_update=row[5] or "",
                pages_touched=json.loads(row[6]) if row[6] else [],
                error=row[7],
                source_name=row[1],
            )

    def get_item(self, source_name: str, item_id: str) -> Item | None:
        row = self._conn.execute(
            "SELECT id, source, path, status, first_seen, last_update, pages_touched, error "
            "FROM items WHERE source = ? AND id = ?",
            (source_name, item_id),
        ).fetchone()
        if not row:
            return None
        return Item(
            item_id=row[0],
            path=row[2] or "",
            status=row[3],
            first_seen=row[4] or "",
            last_update=row[5] or "",
            pages_touched=json.loads(row[6]) if row[6] else [],
            error=row[7],
            source_name=row[1],
        )

    def summary(self, source_name: str) -> SourceSummary:
        cursor_row = self._conn.execute(
            "SELECT cursor FROM sources WHERE name = ?", (source_name,)
        ).fetchone()

        counts = {"pending": 0, "processed": 0, "failed": 0, "skipped": 0}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) FROM items WHERE source = ? GROUP BY status",
            (source_name,),
        ).fetchall():
            if row[0] in counts:
                counts[row[0]] = row[1]

        metrics = self._conn.execute(
            "SELECT AVG(eval_rate), AVG(duration_ms), COUNT(eval_rate) "
            "FROM items WHERE source = ? AND eval_rate IS NOT NULL",
            (source_name,),
        ).fetchone()

        return SourceSummary(
            name=source_name,
            cursor=(cursor_row[0] if cursor_row else None),
            pending=counts["pending"],
            processed=counts["processed"],
            failed=counts["failed"],
            skipped=counts["skipped"],
            avg_tok_s=float(metrics[0] or 0),
            avg_duration_s=float(metrics[1] or 0) / 1000,
            total_items_with_metrics=int(metrics[2] or 0),
        )

    def reset_source(self, source_name: str) -> None:
        self._conn.execute("DELETE FROM items WHERE source = ?", (source_name,))
        self._conn.execute(
            "INSERT OR REPLACE INTO sources (name, cursor) VALUES (?, NULL)",
            (source_name,),
        )
        self._commit()

    # ---- knowledge graph (typed relationships between pages) ----

    def add_relationship(
        self,
        source_page: str,
        target_page: str,
        relation_type: str,
        confidence: str = "medium",
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO relationships
               (source_page, target_page, relation_type, confidence, valid_from, valid_to)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_page, target_page, relation_type, confidence, valid_from, valid_to),
        )
        self._commit()

    def log_query(
        self,
        question: str,
        cited_pages: list[str],
        eval_rate: float,
        duration_ms: float,
        retrieval_mode: str,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            "INSERT INTO query_log (question, cited_pages, eval_rate, duration_ms, retrieval_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (question, json.dumps(cited_pages), round(eval_rate, 1) if eval_rate else None, round(duration_ms), retrieval_mode, now),
        )
        self._commit()

    def query_stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), AVG(eval_rate), AVG(duration_ms) FROM query_log"
        ).fetchone()
        return {
            "total_queries": row[0] or 0,
            "avg_tok_s": round(row[1] or 0, 1),
            "avg_duration_s": round((row[2] or 0) / 1000, 1),
        }

    def is_near_duplicate(self, body: str, source_name: str, item_id: str) -> bool:
        """Check if content is a near-duplicate of an already-ingested item."""
        h = hashlib.sha256(body.strip()[:500].encode()).hexdigest()[:32]
        existing = self._conn.execute(
            "SELECT item_id FROM content_hashes WHERE hash = ?", (h,)
        ).fetchone()
        if existing:
            return True
        self._conn.execute(
            "INSERT OR IGNORE INTO content_hashes (hash, source, item_id) VALUES (?, ?, ?)",
            (h, source_name, item_id),
        )
        self._commit()
        return False

    def get_temporal_cohort(self, page_title: str) -> list[str]:
        """Get pages co-created in the same ingestion session as this page."""
        link = f"[[{page_title}]]"
        rows = self._conn.execute(
            "SELECT pages_touched FROM items WHERE pages_touched LIKE ?",
            (f"%{link}%",),
        ).fetchall()
        cohort: set[str] = set()
        for row in rows:
            for p in json.loads(row[0] or "[]"):
                if p != link:
                    cohort.add(p)
        return sorted(cohort)

    def get_relationships(self, page: str) -> list[dict]:
        """Get all relationships where this page is source or target."""
        rows = self._conn.execute(
            """SELECT source_page, target_page, relation_type, confidence, valid_from, valid_to
               FROM relationships
               WHERE source_page = ? OR target_page = ?""",
            (page, page),
        ).fetchall()
        return [
            {
                "source": r[0], "target": r[1], "type": r[2],
                "confidence": r[3], "valid_from": r[4], "valid_to": r[5],
            }
            for r in rows
        ]


class _BatchContext:
    def __init__(self, manifest: Manifest) -> None:
        self._mf = manifest

    def __enter__(self) -> Manifest:
        self._mf._batch_depth += 1
        return self._mf

    def __exit__(self, *args) -> None:
        self._mf._batch_depth -= 1
        if self._mf._batch_depth == 0:
            self._mf._conn.commit()

