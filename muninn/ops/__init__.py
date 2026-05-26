"""Muninn operations — shared utilities."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def safe_date(value: Any) -> datetime:
    """Parse a date string to datetime, falling back to epoch on failure."""
    if not value:
        return datetime(1970, 1, 1)
    if isinstance(value, datetime):
        return value
    s = str(value)
    try:
        return datetime.fromisoformat(s) if "T" in s else datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return datetime(1970, 1, 1)
