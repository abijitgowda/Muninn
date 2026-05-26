"""Provenance tracking utilities — shared by pipeline, lint, and consolidate."""

from __future__ import annotations

import re

_PROVENANCE_MARKER_RE = re.compile(r"\^\[(extracted|inferred|ambiguous)\]")


def compute_provenance(claims: list[dict]) -> dict[str, float]:
    if not claims:
        return {}
    counts = {"extracted": 0, "inferred": 0, "ambiguous": 0}
    for c in claims:
        prov = c.get("provenance", "extracted")
        if prov in counts:
            counts[prov] += 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(v / total, 2) for k, v in counts.items() if v > 0}


def aggregate_confidence(claims: list[dict]) -> str | None:
    if not claims:
        return None
    scores = {"high": 3, "medium": 2, "low": 1}
    total = sum(scores.get(c.get("confidence", "medium"), 2) for c in claims)
    avg = total / len(claims)
    if avg >= 2.5:
        return "high"
    if avg >= 1.5:
        return "medium"
    return "low"


def extract_inline_provenance(body: str) -> list[dict]:
    """Parse ^[extracted/inferred/ambiguous] markers from page body."""
    claims = []
    for match in _PROVENANCE_MARKER_RE.finditer(body):
        claims.append({"provenance": match.group(1)})
    return claims
