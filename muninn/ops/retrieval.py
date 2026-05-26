"""Retrieval layer for `muninn query` — keyword, vector, RRF, and rerank.

Separated from query.py so that orchestration/synthesis logic stays slim.
All functions here are internal (underscore-prefixed); the public API lives
in query.py which imports the three entry-points it needs:

    from .retrieval import _adaptive_retrieve, _gather_context, _expand_followup
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ollama import Ollama
from ..vault import Page, Vault

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Follow-up detection
# ---------------------------------------------------------------------------

_FOLLOWUP_PRONOUNS = {
    "it",
    "its",
    "they",
    "their",
    "them",
    "this",
    "that",
    "these",
    "those",
    "he",
    "his",
    "she",
    "her",
}
_FOLLOWUP_PHRASES = {
    "what about",
    "how about",
    "tell me more",
    "and",
    "also",
    "what else",
    "anything else",
    "more on",
}


def _is_followup(question: str) -> bool:
    """Detect if a question is a vague follow-up that needs expansion."""
    q = question.strip().lower()
    words = q.split()
    word_set = set(words)
    if len(words) <= 4 and word_set & _FOLLOWUP_PRONOUNS:
        return True
    if any(q.startswith(p) for p in _FOLLOWUP_PHRASES):
        return True
    if len(words) == 1:
        return True
    return False


def _expand_followup(question: str, history_context: str) -> str:
    """Expand a vague follow-up using prior conversation context.

    Brain analogy: anaphora resolution — "their" maps to "NVIDIA" because
    it's in working memory from the prior exchange.
    """
    if not history_context or not _is_followup(question):
        return question

    # Extract key nouns from the prior conversation (capitalized words, likely entities/topics)
    history_words = history_context.split()
    key_terms: list[str] = []
    seen: set[str] = set()
    # First: extract [[wikilinks]] from prior responses — these are guaranteed page titles
    for m in re.finditer(r"\[\[([^\]|#]+)", history_context):
        term = m.group(1).strip()
        if term.lower() not in seen and len(term) >= 2:
            seen.add(term.lower())
            key_terms.append(term)
        if len(key_terms) >= 5:
            break

    # Then: capitalized words as fallback (named entities)
    _noise = {
        "USER",
        "ASSISTANT",
        "SYSTEM",
        "Sources",
        "Confidence",
        "Based",
        "The",
        "This",
        "That",
        "From",
        "See",
        "Wiki",
    }
    for w in history_words:
        clean = w.strip(".,!?:;\"'()[]{}").strip()
        if len(clean) >= 3 and clean[0].isupper() and clean not in _noise and clean.lower() not in seen:
            seen.add(clean.lower())
            key_terms.append(clean)
        if len(key_terms) >= 8:
            break

    if not key_terms:
        return question

    expanded = f"{question} (context: {', '.join(key_terms)})"
    log.info("follow-up expanded: %r → %r", question, expanded)
    return expanded


# ---------------------------------------------------------------------------
# Adaptive (tiered) retrieval
# ---------------------------------------------------------------------------

# Import QueryContext lazily to avoid circular imports — it lives in query.py
# which imports us.  We use TYPE_CHECKING so the annotation resolves at
# type-check time but the actual import happens at runtime only when needed.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .query import QueryContext


def _detect_query_type(question: str) -> str:
    """Detect if the question is episodic (recent/personal), semantic (timeless), or hybrid.

    Maps to the brain's hippocampal (episodic) vs cortical (semantic) retrieval pathways.
    """
    q = question.lower()
    episodic_signals = [
        "recently",
        "lately",
        "today",
        "yesterday",
        "last week",
        "this week",
        "this month",
        "been reading",
        "been learning",
        "i read",
        "i saw",
        "i watched",
        "i wrote",
        "i noted",
        "what did i",
        "what have i",
        "browsing history",
        "reading history",
        "my history",
        "my journal",
        "my notes",
        "journal entry",
    ]
    if any(sig in q for sig in episodic_signals):
        return "episodic"
    semantic_signals = ["what is", "how does", "explain", "define", "compare", "difference between"]
    if any(sig in q for sig in semantic_signals):
        return "semantic"
    return "hybrid"


def _detect_source_filter(question: str, ctx: QueryContext) -> list[str] | None:
    """Detect if the question references a specific source and return matching source names.

    Matches against configured source names, types, and common synonyms
    derived from the config — no hardcoded signal lists.
    """
    q = question.lower()

    # Include built-in sources that are auto-added at ingest time
    from ..config import SourceConfig

    all_sources = list(ctx.config.sources)
    vault = ctx.config.settings.vault_path
    if (vault / "Journal").exists():
        all_sources.append(
            SourceConfig(name="vault-journal", type="folder", tool="muninn.sources.folder:FolderSource")
        )
    if (vault / "Inbox").exists():
        all_sources.append(
            SourceConfig(name="vault-inbox", type="inbox", tool="muninn.sources.inbox:InboxSource")
        )

    source_scores: dict[str, int] = {}
    for s in all_sources:
        name_words = set(s.name.lower().replace("-", " ").replace("_", " ").split())
        type_words = set(s.type.lower().replace("-", " ").replace("_", " ").split())
        all_terms = name_words | type_words
        # Drop noise words that would match too broadly
        all_terms -= {"vault", "source", "all", "the", "my", "a"}

        score = 0
        for term in all_terms:
            if len(term) >= 3 and term in q:
                score += 1
        if score > 0:
            source_scores[s.name] = score

    if not source_scores:
        return None

    # Return sources with the highest match score
    max_score = max(source_scores.values())
    matched = [name for name, score in source_scores.items() if score == max_score]
    return matched or None


def _hippocampal_recall(
    question: str,
    ctx: QueryContext,
    idx_text: str,
    *,
    deep: bool = False,
) -> list[tuple[str, str]] | None:
    """Brain's episodic retrieval: recent experiences → activated entities."""
    import json as _json
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=30)).isoformat()

    source_filter = _detect_source_filter(question, ctx)
    if source_filter:
        placeholders = ",".join("?" for _ in source_filter)
        rows = ctx.manifest._conn.execute(
            f"SELECT pages_touched, last_update FROM items "
            f"WHERE status = 'processed' AND last_update > ? AND source IN ({placeholders}) "
            f"ORDER BY last_update DESC LIMIT 200",
            (cutoff, *source_filter),
        ).fetchall()
        log.debug("hippocampal_recall: source_filter=%s, rows=%d", source_filter, len(rows))
    else:
        rows = ctx.manifest._conn.execute(
            "SELECT pages_touched, last_update FROM items "
            "WHERE status = 'processed' AND last_update > ? "
            "ORDER BY last_update DESC LIMIT 200",
            (cutoff,),
        ).fetchall()

    if not rows:
        return None

    activated: dict[str, int] = {}
    for pages_json, _ in rows:
        for link in _json.loads(pages_json or "[]"):
            title = link.strip("[]").strip()
            if title:
                activated[title] = activated.get(title, 0) + 1
    if not activated:
        return None

    ranked_titles = sorted(activated.items(), key=lambda x: -x[1])
    if source_filter:
        relevant = ranked_titles
    else:
        q_words = {w.lower() for w in question.split() if len(w) > 2}
        if q_words:
            relevant = [(t, c) for t, c in ranked_titles if any(w in t.lower() for w in q_words)]
            if not relevant:
                relevant = ranked_titles
        else:
            relevant = ranked_titles

    n_bodies = ctx.config.settings.max_retrieval_pages
    catalog_lines = [f"Recently activated pages ({len(relevant)} from last 30 days):"]
    pages_to_include = []

    for title, count in relevant:
        page = ctx.vault.find_page(title)
        if page:
            catalog_lines.append(
                f"- [[{page.title}]] ({page.kind}, {count}x): {page.frontmatter.get('summary', '')[:80]}"
            )
            if len(pages_to_include) < n_bodies:
                pages_to_include.append(page)

    out: list[tuple[str, str]] = [("Wiki/index.md", idx_text)]
    out.append(("hippocampal-recall", "\n".join(catalog_lines)))
    for p in pages_to_include:
        out.append((_rel(p.path, ctx.vault.root), p.body))

    spread = _spread_activation(pages_to_include[:3], ctx, n_extra=2)
    seen = {p.path for p in pages_to_include}
    for p in spread:
        if p.path not in seen and len(out) <= n_bodies:
            out.append((_rel(p.path, ctx.vault.root), p.body))
            seen.add(p.path)

    return out


def _filter_by_recency(pages: list[Page], max_age_days: int | None) -> list[Page]:
    """Filter pages by age. None = no filter (all-time)."""
    if max_age_days is None:
        return pages
    now = datetime.now()
    result = []
    for p in pages:
        updated = p.frontmatter.get("updated")
        if not updated:
            continue
        try:
            days_ago = (now - datetime.strptime(str(updated)[:10], "%Y-%m-%d")).days
            if days_ago <= max_age_days:
                result.append(p)
        except (ValueError, TypeError):
            continue
    return result


def _adaptive_retrieve(
    question: str,
    ctx: QueryContext,
    idx_text: str,
    *,
    deep: bool = False,
) -> list[tuple[str, str]]:
    """Adaptive retrieval — temporal cascade + schema scoping + escalation.

    1. Detect query type (episodic vs semantic vs hybrid)
    2. For episodic: search recent→older
    3. For semantic: search schema-scoped first, then broaden
    4. Escalate through keyword → vector → rerank as needed
    5. Spread activation to follow wikilink connections
    """
    import time as _time

    from ..vault import STRUCTURAL_KINDS

    t0 = _time.monotonic()
    query_type = _detect_query_type(question)
    log.debug("query_type=%s, question=%r", query_type, question[:80])

    # ---- Episodic: hippocampal recall via manifest — no page loading needed ----
    if query_type == "episodic":
        episodic = _hippocampal_recall(question, ctx, idx_text, deep=deep)
        if episodic:
            log.debug(
                "hippocampal_recall returned %d chunks in %.0fms",
                len(episodic),
                (_time.monotonic() - t0) * 1000,
            )
            return episodic

    all_pages = [p for p in ctx.vault.all_pages() if p.kind not in STRUCTURAL_KINDS]
    log.debug("all_pages loaded: %d pages in %.0fms", len(all_pages), (_time.monotonic() - t0) * 1000)

    if not all_pages:
        return [("Wiki/index.md", idx_text)]

    # ---- Phase 1: Temporal cascade ----
    if query_type == "episodic":
        time_windows = [7, 30, 90, None]
    elif query_type == "semantic":
        time_windows = [None]
    else:
        time_windows = [30, None]

    keyword_candidates = []
    for max_days in time_windows:
        pages = _filter_by_recency(all_pages, max_days) if max_days else all_pages
        if not pages:
            continue
        log.debug("time_window=%s, pages=%d", max_days or "all", len(pages))

        t_kw = _time.monotonic()
        keyword_ranked = _keyword_rank(question, pages)
        log.debug(
            "keyword_rank: %d results in %.0fms, top=%s",
            len(keyword_ranked),
            (_time.monotonic() - t_kw) * 1000,
            [p.title[:30] for p in keyword_ranked[:3]],
        )

        if keyword_ranked:
            keyword_candidates = keyword_ranked
            log.info(
                "adaptive: keyword (%s, window=%s) — %d matches",
                query_type,
                max_days or "all",
                len(keyword_ranked),
            )
            break

    # ---- Phase 2: Always run vector search and fuse via RRF ----
    t_vec = _time.monotonic()
    vector_ranked = _vector_rank(question, all_pages, ctx)
    log.debug("vector_rank: %d results in %.0fms", len(vector_ranked), (_time.monotonic() - t_vec) * 1000)

    if keyword_candidates and vector_ranked:
        candidates = _rrf(keyword_candidates, vector_ranked, k=60)
        log.info(
            "adaptive: hybrid fusion — %d keyword + %d vector → %d fused",
            len(keyword_candidates),
            len(vector_ranked),
            len(candidates),
        )
    elif vector_ranked:
        candidates = vector_ranked
    else:
        candidates = keyword_candidates

    n_candidates = 15 if deep else 10
    candidates = candidates[:n_candidates]

    if not candidates:
        return [("Wiki/index.md", idx_text)]

    # ---- Rerank if ambiguous ----
    top_strength = max((float(c.frontmatter.get("strength", 1.0)) for c in candidates[:5]), default=0)
    need_rerank = len(candidates) > 6 and top_strength < 0.8
    max_pages = ctx.config.settings.max_retrieval_pages
    if need_rerank:
        log.info("adaptive: rerank — candidates ambiguous, asking LLM")
        final = _llm_rerank(question, candidates, ctx.ollama, top_k=max_pages)
    else:
        final = candidates[:max_pages]

    # ---- Spreading activation (within the same page budget) ----
    t_spread = _time.monotonic()
    n_spread = max(max_pages // 3, 1)
    final = _spread_activation(final, ctx, n_extra=n_spread)
    final = final[:max_pages]
    log.debug("spread_activation: %.0fms, final=%d pages", (_time.monotonic() - t_spread) * 1000, len(final))

    log.debug("adaptive_retrieve total: %.0fms", (_time.monotonic() - t0) * 1000)

    out = [("Wiki/index.md", idx_text)]
    for p in final:
        out.append((_rel(p.path, ctx.vault.root), p.body))

    # Episodic enrichment: source-summaries from matched pages (within budget)
    if query_type == "episodic":
        seen = {name for name, _ in out}
        for p in final:
            for src_link in p.frontmatter.get("sources", []):
                title = src_link.strip("[]").strip()
                if title:
                    src_page = ctx.vault.find_page(title)
                    if src_page:
                        rel = _rel(src_page.path, ctx.vault.root)
                        if rel not in seen:
                            out.append((rel, src_page.body))
                            seen.add(rel)
                            if len(out) > max_pages:
                                break

    return out


# ---------------------------------------------------------------------------
# Spreading activation
# ---------------------------------------------------------------------------


def _spread_activation(
    seeds: list[Page], ctx: QueryContext, n_extra: int = 5, max_hops: int = 2
) -> list[Page]:
    """Multi-hop activation propagation with decay.

    Each seed starts with activation=1.0. Each hop decays by 0.5.
    Nodes reached from multiple seeds get cumulative boost.
    Strength gates propagation.
    """
    from ..vault import STRUCTURAL_KINDS, WIKILINK_RE

    activation: dict[str, float] = {}
    page_cache: dict[str, Page] = {}
    seed_titles = {p.title.lower() for p in seeds}

    for seed in seeds:
        page_cache[seed.title.lower()] = seed

    frontier = [(p, 1.0) for p in seeds]
    for hop in range(max_hops):
        decay = 0.5**hop
        next_frontier: list[tuple[Page, float]] = []

        for page, energy in frontier:
            for link_target, _ in WIKILINK_RE.findall(page.body):
                target = link_target.strip()
                if not target:
                    continue
                tl = target.lower()
                if tl not in page_cache:
                    found = ctx.vault.find_page(target)
                    if found and found.kind not in STRUCTURAL_KINDS:
                        page_cache[tl] = found
                    else:
                        continue
                target_page = page_cache[tl]
                strength = float(target_page.frontmatter.get("strength", 1.0))
                signal = energy * decay * max(strength, 0.1)
                activation[tl] = activation.get(tl, 0) + signal
                if hop < max_hops - 1:
                    next_frontier.append((target_page, signal))

            if ctx.manifest:
                try:
                    for rel in ctx.manifest.get_relationships(page.title):
                        related = (
                            rel["target"] if rel["source"].lower() == page.title.lower() else rel["source"]
                        )
                        rl = related.lower()
                        if rl not in page_cache:
                            found = ctx.vault.find_page(related)
                            if found and found.kind not in STRUCTURAL_KINDS:
                                page_cache[rl] = found
                            else:
                                continue
                        target_page = page_cache[rl]
                        strength = float(target_page.frontmatter.get("strength", 1.0))
                        signal = energy * decay * max(strength, 0.1) * 1.5
                        activation[rl] = activation.get(rl, 0) + signal
                        if hop < max_hops - 1:
                            next_frontier.append((target_page, signal))
                except Exception:  # noqa: BLE001
                    pass

        frontier = next_frontier

    ranked = sorted(
        [(title, score) for title, score in activation.items() if title not in seed_titles],
        key=lambda x: -x[1],
    )
    log.debug(
        "activation network: %d nodes, top=%s", len(ranked), [(t[:20], f"{s:.2f}") for t, s in ranked[:5]]
    )

    result = list(seeds)
    for title, _ in ranked[:n_extra]:
        page = page_cache.get(title)
        if page:
            result.append(page)

    return result


# ---------------------------------------------------------------------------
# Gather context (non-adaptive path)
# ---------------------------------------------------------------------------


def _gather_context(
    question: str,
    ctx: QueryContext,
    *,
    deep: bool,
    idx_text: str,
    use_vectors: bool = False,
    use_rerank: bool = False,
) -> list[tuple[str, str]]:
    """Retrieval with configurable depth.

    use_vectors=False → keyword only (fast, 0 LLM calls)
    use_vectors=True  → keyword + vector + RRF (1 embedding batch call)
    use_rerank=True   → keyword + vector + RRF + LLM rerank (+1 LLM call)
    """
    pages = [p for p in ctx.vault.all_pages() if p.kind not in ("doc", "redirect", "source-summary")]
    if not pages:
        return [("Wiki/index.md", idx_text)]

    # ---- Step 1: Keyword scoring (always) ----
    keyword_ranked = _keyword_rank(question, pages)

    # ---- Step 2: Vector scoring (hybrid + reranked modes) ----
    vector_ranked = _vector_rank(question, pages, ctx) if use_vectors else []

    # ---- Step 3: Reciprocal Rank Fusion ----
    if vector_ranked:
        fused = _rrf(keyword_ranked, vector_ranked, k=60)
    else:
        fused = keyword_ranked

    max_pages = ctx.config.settings.max_retrieval_pages
    candidates = fused[: max_pages * 2]

    if not candidates:
        return [("Wiki/index.md", idx_text)]

    if use_rerank:
        final = _llm_rerank(question, candidates, ctx.ollama, top_k=max_pages)
    else:
        final = candidates[:max_pages]

    out: list[tuple[str, str]] = [("Wiki/index.md", idx_text)]
    for p in final:
        out.append((_rel(p.path, ctx.vault.root), p.body))
    return out


# ---------------------------------------------------------------------------
# Step 1: Keyword
# ---------------------------------------------------------------------------

_schema_index_cache: tuple[int, dict[str, str]] = (0, {})


def _build_schema_index(pages: list[Page]) -> dict[str, str]:
    global _schema_index_cache
    if _schema_index_cache[0] == len(pages) and _schema_index_cache[1]:
        return _schema_index_cache[1]
    idx: dict[str, str] = {}
    for p in pages:
        ps = str(p.frontmatter.get("schema", "")).lower()
        if not ps or ps == "general":
            continue
        for w in p.frontmatter.get("title", "").lower().split():
            if len(w) >= 3 and w not in idx:
                idx[w] = ps
    _schema_index_cache = (len(pages), idx)
    return idx


def _keyword_rank(question: str, pages: list[Page]) -> list[Page]:
    q_words = {w.lower() for w in question.split() if len(w) > 2}
    if not q_words:
        return []

    schema_index = _build_schema_index(pages)
    schema_segments = {schema_index[w] for w in q_words if w in schema_index}

    now = datetime.now()

    scored: list[tuple[float, Page]] = []
    for p in pages:
        meta = " ".join(
            [
                str(p.frontmatter.get("title", "")),
                str(p.frontmatter.get("summary", "")),
                " ".join(p.frontmatter.get("tags") or []),
            ]
        ).lower()
        meta_hits = sum(1 for w in q_words if w in meta)
        body_hits = sum(1 for w in q_words if w in p.body.lower())
        score = meta_hits * 5.0 + body_hits

        strength = float(p.frontmatter.get("strength", 1.0))
        score *= max(strength, 0.1)

        page_schema = str(p.frontmatter.get("schema", "")).lower()
        if schema_segments and page_schema:
            if any(seg in page_schema for seg in schema_segments):
                score *= 2.0

        updated = p.frontmatter.get("updated")
        if updated:
            try:
                days_ago = (now - datetime.strptime(str(updated)[:10], "%Y-%m-%d")).days
                if days_ago <= 7:
                    score *= 1.5
                elif days_ago <= 30:
                    score *= 1.2
            except (ValueError, TypeError):
                pass

        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored]


# ---------------------------------------------------------------------------
# Step 2: Vector (ChromaDB-first, fallback to on-the-fly)
# ---------------------------------------------------------------------------

_vectorstore_cache: dict[str, Any] = {}


def _get_vectorstore(vault: Vault, ollama_host: str) -> Any:
    """Lazy cache keyed by vault path — safe for multi-vault and testing."""
    key = str(vault.root)
    if key in _vectorstore_cache:
        return _vectorstore_cache[key]
    try:
        from ..vectorstore import VectorStore

        vs = VectorStore(
            persist_dir=vault.root / ".muninn" / "chroma",
            ollama_host=ollama_host,
        )
        _vectorstore_cache[key] = vs
        return vs
    except ImportError:
        return None


def _vector_rank(
    question: str,
    pages: list[Page],
    ctx: QueryContext,
    embed_model: str = "mxbai-embed-large",
) -> list[Page]:
    vs = _get_vectorstore(ctx.vault, ctx.ollama.host)
    if vs and vs.count > 0:
        return _vector_rank_chroma(question, pages, vs)
    log.info("vector search skipped: ChromaDB empty or unavailable (run `muninn vectorstore-sync`)")
    return []


def _vector_rank_chroma(question: str, pages: list[Page], vs: Any) -> list[Page]:
    """Query ChromaDB — pre-computed vectors, only 1 embedding call for the question."""
    try:
        results = vs.query(question, n_results=min(30, len(pages)))
        page_by_stem = {p.path.stem: p for p in pages}
        ranked = []
        for r in results:
            page = page_by_stem.get(r["id"])
            if page:
                ranked.append(page)
        return ranked
    except Exception as e:  # noqa: BLE001
        log.warning("ChromaDB query failed, falling back: %s", e)
        return []


# ---------------------------------------------------------------------------
# Step 3: Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def _rrf(keyword: list[Page], vector: list[Page], k: int = 60) -> list[Page]:
    scores: dict[str, float] = {}
    page_map: dict[str, Page] = {}

    for rank, p in enumerate(keyword):
        key = str(p.path)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        page_map[key] = p

    for rank, p in enumerate(vector):
        key = str(p.path)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        page_map[key] = p

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [page_map[key] for key, _ in ranked]


# ---------------------------------------------------------------------------
# Step 4: LLM rerank
# ---------------------------------------------------------------------------


def _llm_rerank(
    question: str,
    candidates: list[Page],
    ollama: Ollama,
    top_k: int = 5,
) -> list[Page]:
    numbered = "\n".join(
        f"{i}. [{p.frontmatter.get('title', p.path.stem)}] — {p.frontmatter.get('summary') or ''}"
        for i, p in enumerate(candidates)
    )
    system = (
        "You are a relevance ranker. Given a question and a numbered list of wiki page titles + summaries, "
        "return ONLY a JSON array of the most relevant page numbers (0-indexed), ordered by relevance. "
        f"Return at most {top_k} numbers. Output JSON only, no prose."
    )
    user = f"QUESTION: {question}\n\nCANDIDATES:\n{numbered}"
    try:
        result = ollama.chat_json(system, user, temperature=0.0, num_ctx=4096)
        if isinstance(result, list):
            indices = result
        elif isinstance(result, dict):
            indices = result.get("rankings") or result.get("indices") or result.get("results") or []
        else:
            indices = []
        reranked = []
        seen = set()
        for idx in indices:
            i = int(idx)
            if 0 <= i < len(candidates) and i not in seen:
                reranked.append(candidates[i])
                seen.add(i)
        if reranked:
            return reranked[:top_k]
    except Exception as e:  # noqa: BLE001
        log.warning("LLM rerank failed, falling back to RRF order: %s", e)
    return candidates[:top_k]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
