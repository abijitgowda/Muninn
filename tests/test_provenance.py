"""Provenance tracking utilities."""

from muninn.provenance import aggregate_confidence, compute_provenance, extract_inline_provenance


def test_compute_provenance_empty():
    assert compute_provenance([]) == {}


def test_compute_provenance_all_extracted():
    claims = [{"provenance": "extracted"}, {"provenance": "extracted"}]
    assert compute_provenance(claims) == {"extracted": 1.0}


def test_compute_provenance_mixed():
    claims = [
        {"provenance": "extracted"},
        {"provenance": "extracted"},
        {"provenance": "inferred"},
        {"provenance": "ambiguous"},
    ]
    result = compute_provenance(claims)
    assert result["extracted"] == 0.5
    assert result["inferred"] == 0.25
    assert result["ambiguous"] == 0.25


def test_compute_provenance_defaults_to_extracted():
    claims = [{"statement": "no provenance key"}, {}]
    result = compute_provenance(claims)
    assert result == {"extracted": 1.0}


def test_aggregate_confidence_empty():
    assert aggregate_confidence([]) is None


def test_aggregate_confidence_high():
    claims = [{"confidence": "high"}, {"confidence": "high"}, {"confidence": "medium"}]
    assert aggregate_confidence(claims) == "high"


def test_aggregate_confidence_medium():
    claims = [{"confidence": "medium"}, {"confidence": "medium"}]
    assert aggregate_confidence(claims) == "medium"


def test_aggregate_confidence_low():
    claims = [{"confidence": "low"}, {"confidence": "low"}]
    assert aggregate_confidence(claims) == "low"


def test_extract_inline_provenance():
    body = (
        "- Claim one ^[extracted]\n"
        "- Claim two ^[inferred]\n"
        "- Claim three ^[ambiguous]\n"
        "- Claim four (no marker)\n"
    )
    markers = extract_inline_provenance(body)
    provs = [m["provenance"] for m in markers]
    assert provs == ["extracted", "inferred", "ambiguous"]


def test_extract_inline_provenance_no_markers():
    body = "Just some regular text\n- A bullet\n- Another bullet\n"
    markers = extract_inline_provenance(body)
    assert markers == []
