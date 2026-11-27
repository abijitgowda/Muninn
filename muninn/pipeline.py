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
from .llm import LLMError, LLMMetrics
from .llm.base import LLMProvider
from .manifest import Manifest
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


def _progress_summary(done: int, out_tokens: int, elapsed_s: float, total: int) -> str:
    """' · total 80 tok/s, 190 items/h, ~4h50m left' — aggregate rate across concurrent requests."""
    if done < 2 or elapsed_s <= 0:  # the first completions arrive together; rates aren't meaningful yet
        return ""
    per_hour = done / elapsed_s * 3600
    left_s = (total - done) / per_hour * 3600
    hours, minutes = divmod(int(left_s // 60), 60)
    left = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
    return f" · total {out_tokens / elapsed_s:.0f} tok/s, {per_hour:.0f} items/h, ~{left} left"


@dataclass
class _IngestJob:
    """Everything the LLM stage needs for one raw item, built on the main thread."""

    src_cfg: SourceConfig
    item_id: str
    raw_path: Path
    raw_page: Page
    body_text: str
    schema_hint: str
    existing_titles: str


def instantiate_source(src_cfg: SourceConfig) -> Source:
    cls = src_cfg.load_tool()
    return cls(
        name=src_cfg.name,
        url=src_cfg.url,
        secret=src_cfg.secret,
        options=src_cfg.options,
    )


_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "yet",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "because",
        "if",
        "when",
        "where",
        "how",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "me",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
    }
)

_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]+\)")


_LOCAL_SOURCE_TYPES = frozenset({"folder", "inbox", "claude-history"})


def _is_garbage_body(body: str, frontmatter: dict | None = None, *, source_type: str = "") -> bool:
    """Layered content quality detection.

    Layers 1, 3-6 target web-fetched content (nav menus, login pages, boilerplate).
    Local file sources (folder, inbox, claude-history) skip those layers — only
    truly empty/broken bodies are rejected.
    """
    is_local = source_type in _LOCAL_SOURCE_TYPES

    fm = frontmatter or {}
    url = str(fm.get("source_url", "")).lower()

    # ---- Layer 1: URL patterns (web only) ----
    if not is_local and any(
        seg in url
        for seg in (
            "/login",
            "/signin",
            "/sign-in",
            "/authorize",
            "/oauth",
            "/sso/",
            "/saml/",
            "/callback",
            "/logout",
            "/signup",
            "client_id=",
            "redirect_uri=",
            "response_type=",
        )
    ):
        return True

    # ---- Layer 2: Extraction failure ----
    text = body.strip()
    if not text or text.startswith("# ") and "No body extracted" in text:
        return True
    # Local files: only reject if truly empty (< 20 chars)
    if is_local:
        return len(text) < 20
    if len(text) < 400:
        return True

    # ---- Layers 3-6: web content heuristics (skipped for local files) ----

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
    stop_count = sum(1 for w in words if w in _STOP_WORDS)
    stop_ratio = stop_count / len(words)
    if stop_ratio < 0.15 and len(text) < 2000:
        return True

    # ---- Layer 5: Sentence coherence ----
    sentences = [s.strip() for s in re.split(r"[.!?]+", sample) if s.strip()]
    if len(sentences) >= 3:
        avg_sentence_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_sentence_len < 4:
            return True

    # ---- Layer 6: Link density (Boilerpipe / Dragnet) ----
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
        self.llm = config.settings.create_llm("ingest")
        self._vectorstore = None

    @property
    def vectorstore(self):
        """Lazy-init ChromaDB vector store."""
        if self._vectorstore is None:
            try:
                from .vectorstore import VectorStore

                self._vectorstore = VectorStore.from_settings(self.config.settings)
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
            source.options["max_items"] = max(int(source.options.get("max_items", 50)), limit)

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
            if _is_garbage_body(raw_page.body, raw_page.frontmatter, source_type=src_cfg.type):
                self.manifest.add_item(src_cfg.name, item.id, str(rel))
                self.manifest.update_item(
                    src_cfg.name, item.id, status="skipped", error="garbage body detected"
                )
                skipped_count += 1
            else:
                self.manifest.add_item(src_cfg.name, item.id, str(rel))
                new_count += 1
            latest_ts = max(latest_ts or item.timestamp, item.timestamp)
            if limit and new_count >= limit:
                break
        if latest_ts:
            self.manifest.set_cursor(src_cfg.name, latest_ts)
        self.console.print(
            f"  fetched: {new_count} new items"
            + (f" ({skipped_count} garbage skipped)" if skipped_count else "")
        )

        if dry_run:
            self.console.print("  [yellow]dry-run: skipping LLM step[/yellow]")
            return []

        # Step 3: process pending items (this includes prior failures too).
        # Screen first so excluded/garbage items never reach the LLM and the
        # progress counter reflects real LLM work.
        pending = list(self.manifest.pending(src_cfg.name))
        if pending:
            self.console.print(f"  screening {len(pending)} pending items...")
        skip_counts: dict[str, int] = {}
        to_process = []
        for item in pending:
            reason = self._screen(src_cfg, source, item.path, item.item_id)
            if reason:
                skip_counts[reason] = skip_counts.get(reason, 0) + 1
            else:
                to_process.append(item)
        if skip_counts:
            self.console.print(
                "  skipped: " + ", ".join(f"{n} {reason}" for reason, n in sorted(skip_counts.items()))
            )
        if limit:
            to_process = to_process[:limit]
        return self._process_pending(src_cfg, to_process)

    # ---- per-item stages: screen → build job → extract (LLM) → apply (writes) ----

    def _screen(self, src_cfg: SourceConfig, source: Source | None, raw_rel_path: str, item_id: str) -> str:
        """Return a skip reason (and record it in the manifest), or "" if the item needs the LLM."""
        try:
            raw_page = self.vault.read_page(self.vault.root / raw_rel_path)
        except Exception as e:  # noqa: BLE001 — missing/corrupt raw file
            self.manifest.mark_failed(src_cfg.name, item_id, f"unreadable raw file: {e}")
            return "unreadable"
        if source is not None and source.is_excluded(raw_page.frontmatter):
            self.manifest.update_item(src_cfg.name, item_id, status="skipped", error="excluded by config")
            return "excluded by config"
        if _is_garbage_body(raw_page.body, raw_page.frontmatter, source_type=src_cfg.type):
            self.manifest.update_item(src_cfg.name, item_id, status="skipped", error="garbage body detected")
            return "garbage"
        return ""

    def _process_pending(self, src_cfg: SourceConfig, items: list) -> list[IngestResult]:
        """Run LLM extraction for screened items and write their pages.

        With ingest_concurrency > 1, extractions run on a thread pool (one LLM
        client per thread) while all vault/manifest writes stay on this thread.
        """
        import time as _time

        results: list[IngestResult] = []
        total = len(items)
        if not total:
            return results
        concurrency = max(1, self.config.settings.ingest_concurrency)
        self.console.print(
            f"  processing {total} items through LLM"
            + (f" ({concurrency} concurrent)" if concurrency > 1 else "")
            + "..."
        )

        def on_start(idx: int, item) -> None:  # sequential mode keeps the live "calling LLM" line
            self.console.print(f"  [{idx}/{total}] {Path(item.path).stem[:50]} — calling LLM...", end="")

        t_start = _time.monotonic()
        out_tokens = 0
        consecutive_failures = 0
        stream = self._extractions(src_cfg, items, concurrency, on_start if concurrency == 1 else None)
        try:
            for idx, (item, job, extraction, m, error) in enumerate(stream, 1):
                prefix = "" if concurrency == 1 else f"  [{idx}/{total}] {Path(item.path).stem[:50]} —"
                touched: list[str] = []
                if error is None:
                    try:
                        touched = self._apply(job, extraction)
                    except Exception as e:  # noqa: BLE001
                        error = e
                if error is not None:
                    err = f"{type(error).__name__}: {error}"
                    self.manifest.mark_failed(src_cfg.name, item.item_id, err)
                    results.append(IngestResult(item.item_id, Path(item.path), [], "failed", err))
                    self.console.print(f"{prefix} ✗ {err}")
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        self.console.print(
                            f"  [red]stopping: {consecutive_failures} consecutive failures — "
                            f"{self.llm.host or 'LLM'} may be down[/red]"
                        )
                        break
                    continue
                consecutive_failures = 0
                out_tokens += m.completion_tokens
                self.manifest.mark_processed(
                    src_cfg.name,
                    item.item_id,
                    touched,
                    eval_rate=m.eval_rate,
                    total_duration_ms=m.total_duration_ms,
                )
                results.append(IngestResult(item.item_id, Path(item.path), touched, "processed"))
                if concurrency == 1:
                    perf = (
                        f" ({m.eval_rate:.0f} tok/s, {m.total_duration_ms / 1000:.1f}s)"
                        if m.eval_rate
                        else ""
                    )
                else:
                    # Per-request speed drops when requests share the GPU; what matters is the total.
                    perf = f" ({m.total_duration_ms / 1000:.0f}s, {m.eval_rate:.0f} tok/s per request)"
                    perf += _progress_summary(len(results), out_tokens, _time.monotonic() - t_start, total)
                self.console.print(f"{prefix} ✓ {len(touched)} pages{perf}")
        finally:
            stream.close()

        elapsed = _time.monotonic() - t_start
        n_ok = sum(1 for r in results if r.status == "processed")
        if n_ok and elapsed > 0:
            self.console.print(
                f"  throughput: {n_ok} items in {elapsed / 60:.1f} min — "
                f"{n_ok / elapsed * 3600:.0f} items/hour, {out_tokens / elapsed:.0f} output tok/s aggregate"
            )
        return results

    def _reload_interval(self) -> int:
        """Extractions between planned model reloads; 0 when off or unsupported."""
        every = self.config.settings.lmstudio_reload_every
        return every if every > 0 and hasattr(self.llm, "reload_model") else 0

    def _reload_llm(self) -> None:
        """Reload the ingest model. Only called with no requests in flight."""
        self.console.print(f"  reloading {self.llm.model} (every {self._reload_interval()} items)...", end="")
        try:
            secs = self.llm.reload_model(context_length=self.config.settings.num_ctx_ingest)
            self.console.print(f" done in {secs:.0f}s")
        except Exception as e:  # noqa: BLE001 — a failed reload must not stop the run
            self.console.print(f" [yellow]failed: {e}[/yellow]")

    def _extractions(self, src_cfg: SourceConfig, items: list, concurrency: int, on_start=None):
        """Yield (item, job, extraction, metrics, error) — in completion order when concurrent.

        With `lmstudio_reload_every`, every N items the queue is drained (no new
        submissions until in-flight requests finish), the model is reloaded, and
        work resumes.
        """
        reload_every = self._reload_interval()
        if concurrency <= 1:
            for idx, item in enumerate(items, 1):
                if reload_every and idx > 1 and (idx - 1) % reload_every == 0:
                    self._reload_llm()
                if on_start:
                    on_start(idx, item)
                try:
                    job = self._build_job(src_cfg, item.path, item.item_id)
                    extraction, metrics = self._extract(job, self.llm)
                    yield item, job, extraction, metrics, None
                except Exception as e:  # noqa: BLE001
                    yield item, None, None, None, e
            return

        import threading
        from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

        local = threading.local()

        def run(job: _IngestJob):
            llm = getattr(local, "llm", None)
            if llm is None:  # one client per worker: metrics and connections aren't shared
                llm = local.llm = self.config.settings.create_llm("ingest")
            return self._extract(job, llm)

        pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="muninn-ingest")
        inflight: dict[Future, tuple] = {}
        queue = list(reversed(items))  # pop() from the end = original order
        submitted_since_reload = 0

        def submit_next() -> None:
            nonlocal submitted_since_reload
            if not queue or (reload_every and submitted_since_reload >= reload_every):
                return  # nothing left, or draining before a reload
            item = queue.pop()
            submitted_since_reload += 1
            try:
                job = self._build_job(src_cfg, item.path, item.item_id)  # main thread: reads vault state
                fut = pool.submit(run, job)
            except Exception as e:  # noqa: BLE001
                job, fut = None, Future()
                fut.set_exception(e)
            inflight[fut] = (item, job)

        try:
            for _ in range(concurrency):
                submit_next()
            while inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in done:
                    item, job = inflight.pop(fut)
                    submit_next()  # keep the server's batch full while we write pages
                    try:
                        extraction, metrics = fut.result()
                        yield item, job, extraction, metrics, None
                    except Exception as e:  # noqa: BLE001
                        yield item, job, None, None, e
                if not inflight and queue and reload_every:  # drained for a planned reload
                    self._reload_llm()
                    submitted_since_reload = 0
                    for _ in range(concurrency):
                        submit_next()
        finally:
            # On early stop, drop queued work; in-flight items stay pending in the manifest.
            pool.shutdown(wait=False, cancel_futures=True)

    def ingest_one(self, src_cfg: SourceConfig, raw_rel_path: str, item_id: str) -> list[str]:
        raw_page = self.vault.read_page(self.vault.root / raw_rel_path)
        if _is_garbage_body(raw_page.body, raw_page.frontmatter, source_type=src_cfg.type):
            self.manifest.update_item(src_cfg.name, item_id, status="skipped", error="garbage body detected")
            return []
        job = self._build_job(src_cfg, raw_rel_path, item_id)
        extraction, metrics = self._extract(job, self.llm)
        self.llm.last_metrics = metrics
        return self._apply(job, extraction)

    def _build_job(self, src_cfg: SourceConfig, raw_rel_path: str, item_id: str) -> _IngestJob:
        raw_path = self.vault.root / raw_rel_path
        raw_page = self.vault.read_page(raw_path)
        body_text = raw_page.body.strip()[: self.config.settings.max_body_ingest]
        return _IngestJob(
            src_cfg=src_cfg,
            item_id=item_id,
            raw_path=raw_path,
            raw_page=raw_page,
            body_text=body_text,
            schema_hint=_schema_hint_for_source(src_cfg),
            # Only pages this source actually mentions — offering arbitrary titles
            # leads the model to invent links to unrelated pages.
            existing_titles=self._get_existing_titles(body_text),
        )

    def _extract(self, job: _IngestJob, llm: LLMProvider) -> tuple[dict, LLMMetrics]:
        """LLM stage. Thread-safe: touches only `job` and the given client."""
        import time as _time

        raw_rel = str(job.raw_path.relative_to(self.vault.root))
        args = (raw_rel, job.raw_page.frontmatter, job.body_text, job.schema_hint, job.existing_titles)
        t0 = _time.monotonic()
        two_pass = self.config.settings.two_pass_ingest
        if two_pass:
            extraction, metrics = self._two_pass_extract(*args, llm=llm)
        else:
            extraction = self._single_pass_extract(*args, llm=llm)
            metrics = llm.last_metrics
        log.info(
            "extract: %s %s — %s, %d entities, %d concepts, %d claims, body=%d chars, %.0fms",
            job.src_cfg.name,
            job.item_id[:12],
            "two-pass" if two_pass else "single-pass",
            len(extraction.get("entities", [])),
            len(extraction.get("concepts", [])),
            len(extraction.get("claims", [])),
            len(job.body_text),
            (_time.monotonic() - t0) * 1000,
        )
        return extraction, metrics

    def _apply(self, job: _IngestJob, extraction: dict) -> list[str]:
        """Write stage: entity/concept/topic pages, relationships, source summary, index, log."""
        src_cfg, item_id = job.src_cfg, job.item_id
        raw_path, raw_page = job.raw_path, job.raw_page

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
        self._source_date = (
            raw_page.frontmatter.get("read_date") or raw_page.frontmatter.get("created_at") or None
        )
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
        summary = (
            extraction.get("log_summary")
            or extraction.get("summary")
            or raw_page.frontmatter.get("title", "")
        )
        log_line = (
            f"## [{ts_str}] ingest | {src_cfg.name} | {summary}\n"
            f"- Touched: {', '.join(touched[:8])}{' …' if len(touched) > 8 else ''}\n"
            f"- Raw: [[{raw_path.relative_to(self.vault.root)}]]\n"
            f"- Source: [[{ss_path.relative_to(self.vault.root).with_suffix('').as_posix()}]]"
        )
        self.vault.append_cumulative_log(log_line)

        return touched

    # ---- helpers ----

    def _single_pass_extract(
        self,
        raw_rel: str,
        frontmatter: dict,
        body: str,
        schema_hint: str,
        existing_titles: str,
        *,
        llm: LLMProvider | None = None,
    ) -> dict:
        llm = llm or self.llm
        system = "You are a knowledge extraction agent. Output JSON only."
        user = (
            EXTRACTION_JSON_INSTRUCTION
            + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + (
                f"EXISTING WIKI PAGES mentioned in this source (use [[wikilinks]] where the text is about them):\n{existing_titles}\n\n"
                if existing_titles
                else ""
            )
            + f"Source: {raw_rel}\n"
            + f"Frontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ==="
        )
        try:
            return llm.chat_json(system, user, num_ctx=self.config.settings.num_ctx_ingest)
        except LLMError as e:
            raise RuntimeError(f"LLM extraction failed: {e}") from e

    def _two_pass_extract(
        self,
        raw_rel: str,
        frontmatter: dict,
        body: str,
        schema_hint: str,
        existing_titles: str,
        *,
        llm: LLMProvider | None = None,
    ) -> tuple[dict, LLMMetrics]:
        """Analysis pass then extraction pass. Returns (extraction, combined metrics)."""
        import json as _json

        llm = llm or self.llm
        # ---- Pass 1: chain-of-thought analysis ----
        analysis_system = "You are a knowledge analysis agent. Think deeply about the source content."
        analysis_user = (
            ANALYSIS_JSON_INSTRUCTION
            + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + f"Source: {raw_rel}\nFrontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ==="
        )
        try:
            analysis = llm.chat_json(
                analysis_system, analysis_user, num_ctx=self.config.settings.num_ctx_ingest
            )
        except LLMError as e:
            raise RuntimeError(f"LLM analysis (pass 1) failed: {e}") from e
        metrics_p1 = llm.last_metrics

        # ---- Pass 2: extraction using the analysis ----
        extract_system = "You are a knowledge extraction agent. Output JSON only."
        analysis_block = _json.dumps(analysis, indent=2, default=str)
        extract_user = (
            EXTRACTION_JSON_INSTRUCTION
            + "\n\n"
            + (f"SCHEMA CONTEXT: This source is about {schema_hint}.\n\n" if schema_hint else "")
            + (
                f"EXISTING WIKI PAGES mentioned in this source (use [[wikilinks]] where the text is about them):\n{existing_titles}\n\n"
                if existing_titles
                else ""
            )
            + f"=== YOUR PRIOR ANALYSIS ===\n{analysis_block}\n=== END ANALYSIS ===\n\n"
            + f"Source: {raw_rel}\nFrontmatter: {frontmatter}\n\n"
            + f"=== BODY ===\n{body}\n=== END BODY ==="
        )
        try:
            extraction = llm.chat_json(
                extract_system, extract_user, num_ctx=self.config.settings.num_ctx_ingest
            )
        except LLMError as e:
            raise RuntimeError(f"LLM extraction (pass 2) failed: {e}") from e
        metrics_p2 = llm.last_metrics

        # Merge claims from analysis if extraction didn't produce them
        if not extraction.get("claims") and analysis.get("claims"):
            extraction["claims"] = analysis["claims"]

        # Aggregate metrics from both passes
        metrics = LLMMetrics(
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
        return extraction, metrics

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
            return self._merge_into_existing(
                existing, kind, context, extraction, source_summary_link, source_ref
            )
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
            c
            for c in extraction.get("claims", [])
            if isinstance(c, dict) and c.get("page", "").strip().lower() == title.strip().lower()
        ]
        provenance = (
            compute_provenance(page_claims)
            if page_claims
            else {"extracted": 0.8, "inferred": 0.2, "ambiguous": 0.0}
        )
        confidence = aggregate_confidence(page_claims) or "medium"
        [q for q in extraction.get("open_questions", []) if isinstance(q, str)]

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
                c
                for c in extraction.get("claims", [])
                if isinstance(c, dict) and c.get("page", "").strip().lower() == existing.title.strip().lower()
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
            "source_ref": raw_page.frontmatter.get("source_url")
            or str(raw_path.relative_to(self.vault.root)),
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
                stem
                for stem, path in idx.items()
                if stem not in self._META_STEMS
                and not stem.startswith("log-")
                and not str(path).startswith(str(sources_dir))
            ]

        if body_hint:
            body_lower = body_hint.lower()
            matched = []
            for t in self._existing_titles_cache:
                # cheap substring test first, then whole-word check ("api" ≠ "rapid")
                if t in _STOP_WORDS:
                    continue
                if len(t) >= 3 and t in body_lower and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", body_lower):
                    matched.append(t)
                    if len(matched) >= 200:
                        break
            return ", ".join(matched)

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
