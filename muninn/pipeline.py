"""End-to-end ingestion orchestrator for the Muninn wiki.

Flow for one source:
  1. fetch new items since manifest cursor
  2. write each as Raw/Sources/<source>/<date>/<id>.md, register in manifest as pending
  3. for each pending item: extract → resolve → merge → log → index → mark processed
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from rich.console import Console

from .config import Config, SourceConfig
from .manifest import Manifest
from .ollama import Ollama, LLMMetrics, OllamaError
from .prompts import ANALYSIS_JSON_INSTRUCTION, EXTRACTION_JSON_INSTRUCTION, PromptLoader
from .provenance import aggregate_confidence, compute_provenance, extract_inline_provenance
from .sources.base import Source
from .vault import Page, Vault


@dataclass
class IngestResult:
    item_id: str
    raw_path: Path
    pages_touched: list[str]
    status: str  # processed | failed | skipped
    error: str | None = None


def instantiate_source(src_cfg: SourceConfig) -> Source:
    cls = src_cfg.load_tool()
    return cls(
        name=src_cfg.name,
        url=src_cfg.url,
        secret=src_cfg.secret,
        options=src_cfg.options,
    )


_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both",
    "each", "few", "more", "most", "other", "some", "such", "no",
    "only", "own", "same", "than", "too", "very", "just", "because",
    "if", "when", "where", "how", "what", "which", "who", "whom",
    "this", "that", "these", "those", "it", "its", "he", "she", "they",
    "we", "you", "i", "me", "my", "your", "his", "her", "our", "their",
})

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]+\)")


def _is_garbage_body(body: str, frontmatter: dict | None = None) -> bool:
    """Layered content quality detection.

    Layer 1: URL patterns (auth, login, OAuth)
    Layer 2: Extraction failure (empty, too short)
    Layer 2b: JS-disabled / gated content signals
    Layer 3: Information density (TTR, word count, long tokens)
    Layer 4: Stop-word ratio (nav menus vs prose)
    Layer 5: Sentence coherence (avg sentence length)
    Layer 6: Link density (navigation vs content)
    """
    fm = frontmatter or {}
    url = str(fm.get("source_url", "")).lower()

    # ---- Layer 1: URL patterns ----
    if any(seg in url for seg in (
        "/login", "/signin", "/sign-in", "/authorize", "/oauth",
        "/sso/", "/saml/", "/callback", "/logout", "/signup",
        "client_id=", "redirect_uri=", "response_type=",
    )):
        return True

    # ---- Layer 2: Extraction failure ----
    text = body.strip()
    if not text or text.startswith("# ") and "No body extracted" in text:
        return True
    if len(text) < 400:
        return True

    # ---- Layer 3: Information density ----
    sample = text[:2000]
    words = sample.lower().split()
    if not words:
        return True
    if len(words) < 80:
        return True

    ttr = len(set(words)) / len(words)
    if ttr < 0.25 and len(text) < 1000:
        return True

    long_tokens = sum(1 for w in words if len(w) > 60)
    if long_tokens >= 2:
        return True

    # ---- Layer 4: Stop-word ratio (Google Panda / CETR) ----
    # Real prose: 40-60% stop words. Nav/menus: <15%.
    stop_count = sum(1 for w in words if w in _STOP_WORDS)
    stop_ratio = stop_count / len(words)
    if stop_ratio < 0.15 and len(text) < 2000:
        return True

    # ---- Layer 5: Sentence coherence ----
    # Real articles: avg 10-25 words/sentence. Menu fragments: <4.
    sentences = [s.strip() for s in re.split(r'[.!?]+', sample) if s.strip()]
    if len(sentences) >= 3:
        avg_sentence_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_sentence_len < 4:
            return True

    # ---- Layer 6: Link density (Boilerpipe / Dragnet) ----
    # If >50% of text is markdown link anchors, it's navigation.
    link_text_chars = sum(len(m.group(1)) for m in _LINK_RE.finditer(text))
    if len(text) > 0 and link_text_chars / len(text) > 0.5:
        return True

    return False


_SCHEMA_HINTS = {
    "market": (
        "financial markets and stock analysis. "
        "Extract: revenue figures, EPS, price targets, P/E ratios, market cap, "
        "analyst ratings, earnings dates, competitive positioning, supply chain relationships. "
        "Always include specific numbers (percentages, dollar amounts, dates)."
    ),
    "x": (
        "social media posts and public discourse. "
        "Extract: the author's key claim, any data cited, people/orgs mentioned, "
        "the topic being discussed, engagement signals."
    ),
    "browser_history": (
        "web content the user has been reading. "
        "Extract: the main topic, key insights and evidence, named entities, "
        "and how this connects to the user's existing knowledge areas."
    ),
    "folder": (
        "personal documents. "
        "Extract: document type, key facts, dates, people/organizations involved, "
        "action items, and any financial or legal details."
    ),
}


def _schema_hint_for_source(src_cfg: SourceConfig) -> str:
    """Schema-guided encoding: tell the LLM what domain this source is from."""
    # Per-source hint from wiki.yaml takes priority (user-configurable)
    custom = src_cfg.options.get("schema_hint")
    if custom:
        return str(custom)
    # Fall back to per-type defaults
    return _SCHEMA_HINTS.get(src_cfg.type, "")


class Pipeline:
    def __init__(self, config: Config, console: Console | None = None) -> None:
        self.config = config
        self.console = console or Console()
        self.vault = Vault(config.settings.vault_path)
        self.manifest = Manifest(config.settings.manifest_path)
        self.prompts = PromptLoader(
            skills_dir=config.settings.skills_dir,
            schema_dir=Path(__file__).resolve().parents[1] / "docs" / "Schema",
        )
        self.ollama = Ollama(
            host=config.settings.ollama_host,
            model=config.settings.model_for_ingest,
            timeout=config.settings.ollama_timeout,
        )
        self._vectorstore = None

    @property
    def vectorstore(self):
        """Lazy-init ChromaDB vector store."""
        if self._vectorstore is None:
            try:
                from .vectorstore import VectorStore
                self._vectorstore = VectorStore(
                    persist_dir=self.config.settings.state_dir / "chroma",
                    ollama_host=self.config.settings.ollama_host,
                )
            except ImportError:
                pass
        return self._vectorstore

    # ---- public ----

    def run_source(self, src_cfg: SourceConfig, *, limit: int | None, dry_run: bool) -> list[IngestResult]:
        self.console.print(f"\n[bold cyan]→ {src_cfg.name}[/bold cyan]  ({src_cfg.type})")
        source = instantiate_source(src_cfg)
        cursor = self.manifest.cursor(src_cfg.name)
        self.console.print(f"  cursor: {cursor or 'beginning of time'}")

        # Override the source's max_items when the CLI passes --limit (so the SQL
        # query actually returns enough rows instead of capping at options.max_items).
        if limit:
            source.options["max_items"] = max(
                int(source.options.get("max_items", 50)), limit
            )

        # Step 1+2: pull, write raw, filter garbage at fetch time
        new_count = 0
        skipped_count = 0
        latest_ts: datetime | None = cursor
        for item in source.fetch(cursor):
            if self.manifest.has_item(src_cfg.name, item.id):
                continue
            md = source.to_markdown(item)
            raw_path = self.vault.write_raw(src_cfg.name, item.id, md, date=item.timestamp)
            rel = raw_path.relative_to(self.vault.root)
            raw_page = self.vault.read_page(raw_path)
            if _is_garbage_body(raw_page.body, raw_page.frontmatter):
                self.manifest.add_item(src_cfg.name, item.id, str(rel))
                self.manifest.update_item(src_cfg.name, item.id, status="skipped", error="garbage body detected")
                skipped_count += 1
            else:
                self.manifest.add_item(src_cfg.name, item.id, str(rel))
                new_count += 1
            latest_ts = max(latest_ts or item.timestamp, item.timestamp)
            if limit and new_count >= limit:
                break
        if latest_ts:
            self.manifest.set_cursor(src_cfg.name, latest_ts)
        self.console.print(f"  fetched: {new_count} new items" + (f" ({skipped_count} garbage skipped)" if skipped_count else ""))

        if dry_run:
            self.console.print("  [yellow]dry-run: skipping LLM step[/yellow]")
            return []

        # Step 3: process pending items (this includes prior failures too)
        results: list[IngestResult] = []
        pending = list(self.manifest.pending(src_cfg.name))
        if limit:
            pending = pending[:limit]
        total_pending = len(pending)
        if total_pending:
            self.console.print(f"  processing {total_pending} pending items through LLM...")
        consecutive_failures = 0
        for idx, item in enumerate(pending, 1):
            raw_title = Path(item.path).stem[:50]
            self.console.print(f"  [{idx}/{total_pending}] {raw_title} — calling Ollama...", end="")
            try:
                touched = self.ingest_one(src_cfg, item.path, item.item_id)
                # Check if item was skipped (garbage body)
                item_state = self.manifest.get_item(src_cfg.name, item.item_id)
                if item_state and item_state.status == "skipped":
                    results.append(IngestResult(item.item_id, Path(item.path), [], "skipped"))
                    self.console.print(f" ⊘ skipped (garbage body)")
                    continue
                m = self.ollama.last_metrics
                self.manifest.mark_processed(
                    src_cfg.name, item.item_id, touched,
                    eval_rate=m.eval_rate, total_duration_ms=m.total_duration_ms,
                )
                results.append(IngestResult(item.item_id, Path(item.path), touched, "processed"))
                perf = f" ({m.eval_rate:.0f} tok/s, {m.total_duration_ms/1000:.1f}s)" if m.eval_rate else ""
                self.console.print(f" ✓ {len(touched)} pages{perf}")
                consecutive_failures = 0
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                self.manifest.mark_failed(src_cfg.name, item.item_id, err)
                results.append(IngestResult(item.item_id, Path(item.path), [], "failed", err))
                self.console.print(f" ✗ {err}")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self.console.print(f"  [red]stopping: {consecutive_failures} consecutive failures — Ollama may be down[/red]")
                    break

        return results

    def ingest_one(self, src_cfg: SourceConfig, raw_rel_path: str, item_id: str) -> list[str]:
        raw_path = self.vault.root / raw_rel_path
        raw_page = self.vault.read_page(raw_path)

        # ---- Pre-filter: skip garbage bodies ----
        if _is_garbage_body(raw_page.body, raw_page.frontmatter):
            self.manifest.update_item(src_cfg.name, item_id, status="skipped", error="garbage body detected")
            return []

        schema_hint = _schema_hint_for_source(src_cfg)
        existing_titles = self._get_existing_titles()
        body_text = raw_page.body.strip()[:self.config.settings.max_body_ingest]
        raw_rel = str(raw_path.relative_to(self.vault.root))

        import time as _time
        t0 = _time.monotonic()
        pass_type = "two-pass" if self.config.settings.two_pass_ingest else "single-pass"
        if self.config.settings.two_pass_ingest:
            extraction = self._two_pass_extract(raw_rel, raw_page.frontmatter, body_text, schema_hint, existing_titles)
        else:
            extraction = self._single_pass_extract(raw_rel, raw_page.frontmatter, body_text, schema_hint, existing_titles)
        extract_ms = (_time.monotonic() - t0) * 1000
        n_entities = len(extraction.get("entities", []))
        n_concepts = len(extraction.get("concepts", []))
        n_claims = len(extraction.get("claims", []))
        log.info(
            "extract: %s %s — %s, %d entities, %d concepts, %d claims, body=%d chars, %.0fms",
            src_cfg.name, item_id[:12], pass_type, n_entities, n_concepts, n_claims, len(body_text), extract_ms,
        )

        touched: list[str] = []
        pages_for_index: list[Page] = []

        # ---- Pass 2: write entity + concept + topic pages ----
        is_market = raw_page.frontmatter.get("source_type") == "market" or src_cfg.type == "market"
        concept_kind = src_cfg.options.get("page_kind", "concept")
        _ents = [i for i in extraction.get("entities", []) if isinstance(i, dict)]
        _cons = [c for c in extraction.get("concepts", []) if isinstance(c, dict)]
        _tops = [t for t in extraction.get("topics", []) if isinstance(t, dict)]
        kind_to_list = {
            "person": [(i.get("name"), i.get("context", "")) for i in _ents if i.get("kind") == "person"],
            "org": [(i.get("name"), i.get("context", "")) for i in _ents if i.get("kind") == "org"],
            concept_kind: [(c.get("name"), c.get("context", "")) for c in _cons],
            "topic": [(t.get("name"), t.get("context", "")) for t in _tops],
        }
        source_summary_link = self._source_summary_link(src_cfg.type, raw_page, item_id)
        source_ref = raw_page.frontmatter.get("source_url") or str(raw_path.relative_to(self.vault.root))
        self._source_date = raw_page.frontmatter.get("read_date") or raw_page.frontmatter.get("created_at") or None
        if isinstance(self._source_date, str) and "T" in self._source_date:
            self._source_date = self._source_date.split("T")[0]

        if concept_kind == "project":
            rel_folder = raw_page.frontmatter.get("relative_folder") or ""
            if rel_folder:
                project_name = rel_folder.rstrip("/").split("/")[-1]
                if project_name:
                    page = self._upsert_page(
                        kind="project",
                        title=project_name,
                        context=extraction.get("summary") or "",
                        extraction=extraction,
                        source_summary_link=source_summary_link,
                        source_ref=source_ref,
                    )
                    touched.append(f"[[{page.title}]]")
                    pages_for_index.append(page)

        if is_market:
            ticker = raw_page.frontmatter.get("ticker")
            if ticker and ticker != "TRENDING":
                page = self._upsert_page(
                    kind="org",
                    title=ticker,
                    context=extraction.get("summary") or "",
                    extraction=extraction,
                    source_summary_link=source_summary_link,
                    source_ref=source_ref,
                )
                touched.append(f"[[{page.title}]]")
                pages_for_index.append(page)

        for kind, items in kind_to_list.items():
            for name, context in items:
                if not name:
                    continue
                title = self._normalize_title(name)
                page = self._upsert_page(
                    kind=kind,
                    title=title,
                    context=context,
                    extraction=extraction,
                    source_summary_link=source_summary_link,
                    source_ref=source_ref,
                )
                touched.append(f"[[{page.title}]]")
                pages_for_index.append(page)

        # ---- Pass 2b: store typed relationships in knowledge graph ----
        for rel in extraction.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            src = rel.get("source", "").strip()
            tgt = rel.get("target", "").strip()
            rtype = rel.get("type", "").strip()
            if src and tgt and rtype:
                self.manifest.add_relationship(src, tgt, rtype)

        # ---- Pass 3: source-summary ----
        ss_path = self._write_source_summary(
            src_cfg=src_cfg,
            raw_page=raw_page,
            raw_path=raw_path,
            extraction=extraction,
            touched=touched,
            item_id=item_id,
        )
        touched.append(f"[[{ss_path.stem}]]")
        pages_for_index.append(self.vault.read_page(ss_path))

        # ---- Batch: update index ----
        for p in pages_for_index:
            self.vault.update_index_for(p)

        # ---- Pass 4: append logs ----
        ts_str = datetime.now().strftime("%H:%M")
        summary = extraction.get("log_summary") or extraction.get("summary") or raw_page.frontmatter.get("title", "")
        log_line = (
            f"## [{ts_str}] ingest | {src_cfg.name} | {summary}\n"
            f"- Touched: {', '.join(touched[:8])}{' …' if len(touched) > 8 else ''}\n"
            f"- Raw: [[{raw_path.relative_to(self.vault.root)}]]\n"
            f"- Source: [[{ss_path.relative_to(self.vault.root).with_suffix('').as_posix()}]]"
        )
        self.vault.append_cumulative_log(log_line)

        return touched

    # ---- helpers ----

    def _single_pass_extract(self, raw_rel: str, frontmatter: dict, body: str, schema_hint: str, existing_titles: str) -> dict:
        system = "You are a knowledge extraction agent. Output JSON only."
        user = (
            EXTRACTION_JSON_INSTRUCTION + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + (f"EXISTING WIKI PAGES (reference these as [[wikilinks]] in your context fields):\n{existing_titles}\n\n" if existing_titles else "")
            + f"Source: {raw_rel}\n"
            + f"Frontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ==="
        )
        try:
            return self.ollama.chat_json(system, user, num_ctx=self.config.settings.num_ctx_ingest)
        except OllamaError as e:
            raise RuntimeError(f"Ollama extraction failed: {e}") from e

    def _two_pass_extract(self, raw_rel: str, frontmatter: dict, body: str, schema_hint: str, existing_titles: str) -> dict:
        import json as _json

        # ---- Pass 1: chain-of-thought analysis ----
        analysis_system = "You are a knowledge analysis agent. Think deeply about the source content."
        analysis_user = (
            ANALYSIS_JSON_INSTRUCTION + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + f"Source: {raw_rel}\nFrontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ==="
        )
        try:
            analysis = self.ollama.chat_json(analysis_system, analysis_user, num_ctx=self.config.settings.num_ctx_ingest)
        except OllamaError as e:
            raise RuntimeError(f"Ollama analysis (pass 1) failed: {e}") from e
        metrics_p1 = self.ollama.last_metrics

        # ---- Pass 2: extraction using the analysis ----
        extract_system = "You are a knowledge extraction agent. Output JSON only."
        analysis_block = _json.dumps(analysis, indent=2, default=str)
        extract_user = (
            EXTRACTION_JSON_INSTRUCTION + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + (f"EXISTING WIKI PAGES (reference as [[wikilinks]]):\n{existing_titles}\n\n" if existing_titles else "")
            + f"=== YOUR PRIOR ANALYSIS ===\n{analysis_block}\n=== END ANALYSIS ===\n\n"
            + f"Source: {raw_rel}\nFrontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ===\n/no_think"
        )
        try:
            extraction = self.ollama.chat_json(extract_system, extract_user, num_ctx=self.config.settings.num_ctx_ingest)
        except OllamaError as e:
            raise RuntimeError(f"Ollama extraction (pass 2) failed: {e}") from e
        metrics_p2 = self.ollama.last_metrics

        # Merge claims from analysis if extraction didn't produce them
        if not extraction.get("claims") and analysis.get("claims"):
            extraction["claims"] = analysis["claims"]

        # Aggregate metrics from both passes
        self.ollama.last_metrics = LLMMetrics(
            model=metrics_p2.model,
            prompt_tokens=metrics_p1.prompt_tokens + metrics_p2.prompt_tokens,
            completion_tokens=metrics_p1.completion_tokens + metrics_p2.completion_tokens,
            total_duration_ms=metrics_p1.total_duration_ms + metrics_p2.total_duration_ms,
            prompt_eval_rate=0,
            eval_rate=(
                (metrics_p1.completion_tokens + metrics_p2.completion_tokens)
                / max((metrics_p1.total_duration_ms + metrics_p2.total_duration_ms) / 1000, 0.001)
            ),
        )
        return extraction

    def _upsert_page(
        self,
        *,
        kind: str,
        title: str,
        context: str,
        extraction: dict[str, Any],
        source_summary_link: str,
        source_ref: str,
    ) -> Page:
        existing = self.vault.find_page(title)
        if existing:
            return self._merge_into_existing(existing, kind, context, extraction, source_summary_link, source_ref)
        return self._create_new(kind, title, context, extraction, source_summary_link, source_ref)

    def _create_new(
        self,
        kind: str,
        title: str,
        context: str,
        extraction: dict[str, Any],
        source_summary_link: str,
        source_ref: str,
    ) -> Page:
        today = datetime.now().date().isoformat()
        source_date = self._source_date or today

        # ---- body: LLM controls structure, pipeline just assembles ----
        body_text = context or extraction.get("summary", "")
        body_text = self._strip_dangling_wikilinks(body_text, extraction)

        body_lines = [f"# {title}", "", body_text, ""]

        # Source attribution
        if source_ref.startswith("http"):
            body_lines += ["## Sources", f"- {source_summary_link} — [{source_ref}]({source_ref})", ""]
        else:
            body_lines += ["## Sources", f"- {source_summary_link}", ""]

        # ---- frontmatter: pipeline controls system fields ----
        page_claims = [
            c for c in extraction.get("claims", [])
            if isinstance(c, dict)
            and c.get("page", "").strip().lower() == title.strip().lower()
        ]
        provenance = compute_provenance(page_claims) if page_claims else {"extracted": 0.8, "inferred": 0.2, "ambiguous": 0.0}
        confidence = aggregate_confidence(page_claims) or "medium"
        open_qs = [q for q in extraction.get("open_questions", []) if isinstance(q, str)]

        schema = self._derive_schema(extraction, kind, title)
        tags = [seg for seg in schema.split("/") if seg] + [kind]

        fm: dict[str, Any] = {
            "title": title,
            "kind": kind,
            "summary": extraction.get("summary", "") or body_text[:200],
            "tags": tags,
            "sources": [source_summary_link],
            "created": source_date,
            "updated": today,
            "confidence": confidence,
            "lifecycle": "draft",
            "provenance": provenance,
            "schema": schema,
            "strength": 1.0,
            "access_count": 0,
            "last_accessed": None,
            "consolidation_count": 0,
        }

        page = Page(
            path=self.vault.page_path(kind, title),
            frontmatter=fm,
            body="\n".join(body_lines),
        )
        self.vault.write_page(page)
        self._vectorstore_upsert(page)
        if self._existing_titles_cache is not None:
            stem = page.path.stem.lower()
            if stem not in self._existing_titles_cache:
                self._existing_titles_cache.append(stem)
        return page

    def _merge_into_existing(
        self,
        existing: Page,
        kind: str,
        context: str,
        extraction: dict[str, Any],
        source_summary_link: str,
        source_ref: str,
    ) -> Page:
        # Lightweight merge — keep existing body, append a new note + register source.
        # An LLM-driven semantic merge could replace this; for v0, deterministic merge is safer.
        today = datetime.now().date().isoformat()
        sources = list(existing.frontmatter.get("sources") or [])
        if source_summary_link not in sources:
            sources.append(source_summary_link)
        existing.frontmatter["sources"] = sources
        existing.frontmatter["updated"] = today
        existing.frontmatter.setdefault("kind", kind)
        # Boost strength on re-touch (spaced repetition: revisited = stronger)
        strength = float(existing.frontmatter.get("strength", 1.0))
        existing.frontmatter["strength"] = round(min(strength + 0.2, 2.0), 3)

        addendum_marker = f"<!-- ingest:{source_summary_link} -->"
        if addendum_marker not in existing.body:
            addendum_text = context or extraction.get("summary", "")
            lines = [
                f"\n\n{addendum_marker}",
                f"### From {source_summary_link} ({today})",
                "",
                addendum_text,
            ]
            incoming_claims = [
                c for c in extraction.get("claims", [])
                if isinstance(c, dict)
                and c.get("page", "").strip().lower() == existing.title.strip().lower()
            ]
            if incoming_claims:
                lines.append("")
                for claim in incoming_claims:
                    prov = claim.get("provenance", "extracted")
                    marker = f" ^[{prov}]" if prov != "extracted" else ""
                    lines.append(f"- {claim.get('statement', '')}{marker}")
            lines.append("")
            existing.body = existing.body.rstrip() + "\n".join(lines)

        # Recompute page-level provenance from all inline markers
        all_markers = extract_inline_provenance(existing.body)
        if all_markers:
            existing.frontmatter["provenance"] = compute_provenance(all_markers)

        self.vault.write_page(existing)
        self._vectorstore_upsert(existing)
        return existing

    def _write_source_summary(
        self,
        *,
        src_cfg: SourceConfig,
        raw_page: Page,
        raw_path: Path,
        extraction: dict[str, Any],
        touched: list[str],
        item_id: str,
    ) -> Path:
        today = datetime.now().date().isoformat()
        slug_basis = raw_page.frontmatter.get("title") or item_id
        slug = Source.slugify(slug_basis)  # type: ignore[attr-defined]
        src_slug = src_cfg.type.replace("_", "-")
        title = f"{today}-{src_slug}-{slug}"
        kind = "source-summary"
        path = self.vault.folder_for_kind(kind) / f"{title}.md"

        fm = {
            "title": title,
            "kind": kind,
            "summary": extraction.get("summary") or "",
            "tags": ["source", src_cfg.type],
            "source_type": src_cfg.type,
            "source_ref": raw_page.frontmatter.get("source_url") or str(raw_path.relative_to(self.vault.root)),
            "source_name": src_cfg.name,
            "read_date": raw_page.frontmatter.get("read_date") or today,
            "ingested_at": datetime.now().isoformat(timespec="seconds"),
            "lifecycle": "pinned",
            "pages_touched": touched,
            "raw": f"[[{raw_path.relative_to(self.vault.root)}]]",
        }
        page_body = extraction.get("page_body", "")
        main_content = page_body if page_body and len(page_body) > 100 else extraction.get("summary", "")
        body = (
            f"# {raw_page.frontmatter.get('title', title)}\n\n"
            "## What it is\n"
            f"{main_content}\n\n"
            "## Open questions raised\n"
            + "\n".join(f"- {q}" for q in extraction.get("open_questions", [])[:5])
            + "\n\n## Pages updated\n"
            + "\n".join(f"- {t}" for t in touched)
            + "\n\n## Raw\n"
            + f"See [[{raw_path.relative_to(self.vault.root)}]]\n"
        )
        page = Page(path=path, frontmatter=fm, body=body)
        self.vault.write_page(page)
        return path

    def _source_summary_link(self, src_type: str, raw_page: Page, item_id: str) -> str:
        today = datetime.now().date().isoformat()
        slug_basis = raw_page.frontmatter.get("title") or item_id
        slug = Source.slugify(slug_basis)  # type: ignore[attr-defined]
        src_slug = src_type.replace("_", "-")
        return f"[[{today}-{src_slug}-{slug}]]"

    _existing_titles_cache: list[str] | None = None

    _META_STEMS = frozenset({"index", "overview", "log", "welcome", "readme"})

    def _get_existing_titles(self, body_hint: str = "") -> str:
        """Get relevant existing wiki page titles for the LLM to reference.

        Excludes structural pages (source-summaries, logs, index, overview)
        so the LLM only wikilinks real knowledge pages.
        """
        if self._existing_titles_cache is None:
            idx = self.vault._ensure_slug_index()
            sources_dir = self.vault.wiki / "Sources"
            self._existing_titles_cache = [
                stem for stem, path in idx.items()
                if stem not in self._META_STEMS
                and not stem.startswith("log-")
                and not str(path).startswith(str(sources_dir))
            ]

        if body_hint:
            body_lower = body_hint.lower()
            matched = [t for t in self._existing_titles_cache if t in body_lower and len(t) >= 3]
            return ", ".join(matched[:200])

        return ", ".join(self._existing_titles_cache[:200])

    def _vectorstore_upsert(self, page: Page) -> None:
        """Embed and store page in ChromaDB. No-op if chromadb not installed."""
        vs = self.vectorstore
        if not vs:
            return
        try:
            vs.upsert_page(
                page_id=page.path.stem,
                title=page.frontmatter.get("title", page.path.stem),
                summary=page.frontmatter.get("summary", ""),
                kind=page.kind,
                schema=page.frontmatter.get("schema", ""),
                strength=float(page.frontmatter.get("strength", 1.0)),
                tags=page.frontmatter.get("tags"),
            )
        except Exception as e:  # noqa: BLE001
            log.debug("vectorstore upsert failed for %s: %s", page.path.stem, e)

    def _derive_schema(self, extraction: dict[str, Any], kind: str, title: str) -> str:
        """Auto-assign a hierarchical schema path from the LLM extraction.

        The LLM provides a per-source schema and per-entity/concept schemas.
        We pick the most specific one that matches this page's title.
        """
        # Check if the extraction has a per-item schema matching this title
        for group in ("entities", "concepts"):
            for item in extraction.get(group, []):
                if item.get("name", "").strip().lower() == title.strip().lower():
                    s = item.get("schema", "")
                    if s:
                        return s.strip().lower()
        # Fall back to the source-level schema
        source_schema = extraction.get("schema", "")
        if source_schema:
            return source_schema.strip().lower()
        # Last resort: derive from kind
        return {"project": "projects"}.get(kind, "general")

    def _strip_dangling_wikilinks(self, text: str, extraction: dict) -> str:
        """Remove [[wikilinks]] that point to pages that don't exist and won't be created."""
        from .vault import WIKILINK_RE
        names_in_extraction = {
            item.get("name", "").strip().lower()
            for group in ("entities", "concepts", "topics")
            for item in extraction.get(group, [])
            if isinstance(item, dict) and item.get("name")
        }
        def _replace(m):
            target = m.group(1).strip()
            if self.vault.find_page(target):
                return m.group(0)
            if target.lower() in names_in_extraction:
                return m.group(0)
            return target
        return WIKILINK_RE.sub(_replace, text)

    @staticmethod
    def _normalize_title(name: str) -> str:
        return " ".join(str(name).split())[:160]
