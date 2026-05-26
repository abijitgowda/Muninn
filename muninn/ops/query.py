"""`muninn query` — hybrid retrieval against the wiki.

Retrieval pipeline:
  1. Keyword scoring (BM25-lite: term overlap on title + summary + tags + body)
  2. Vector similarity (mxbai-embed-large via Ollama /v1/embeddings)
  3. Reciprocal Rank Fusion (RRF) to merge both ranked lists
  4. LLM reranking — ask the model to pick the top-K most relevant candidates
  5. Synthesis — answer the question using the reranked context

Exports `answer_query()` so the HTTP server can reuse the same retrieval + synthesis path.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from rich.console import Console

from ..config import Config
from ..manifest import Manifest
from ..ollama import Ollama, OllamaError
from ..prompts import PromptLoader
from ..vault import Vault

from .retrieval import _adaptive_retrieve, _gather_context, _expand_followup

log = logging.getLogger(__name__)

_vault_cache: dict[str, Vault] = {}
_manifest_cache: dict[str, Manifest] = {}


def _get_vault(config: Config) -> Vault:
    key = str(config.settings.vault_path)
    v = _vault_cache.get(key)
    if v is None:
        v = Vault(config.settings.vault_path)
        _vault_cache[key] = v
    return v


def _get_manifest(config: Config) -> Manifest:
    key = str(config.settings.manifest_path)
    m = _manifest_cache.get(key)
    if m is None:
        m = Manifest(config.settings.manifest_path)
        _manifest_cache[key] = m
    return m


@dataclass
class QueryContext:
    """Carries all dependencies through the query call chain. No globals."""
    config: Config
    vault: Vault
    ollama: Ollama
    prompts: PromptLoader
    manifest: Manifest


@dataclass
class QueryResult:
    question: str
    answer: str
    cited_pages: list[str] = field(default_factory=list)        # rel-paths of pages read
    history_context: str = ""                                    # prior conversation, if any


def _resolve_question(
    vault: Vault,
    question: str | None,
    explain: str | None,
) -> str | None:
    if explain:
        title = explain.strip().strip("[]").strip()
        if not vault.find_page(title):
            return None
        return f"Explain the page [[{title}]] using its content and sources."
    return question


def _build_user_prompt(
    question: str,
    context_chunks: list[tuple[str, str]],
    history_context: str = "",
    max_body: int = 8000,
    max_total_chars: int = 24000,
) -> str:
    user_parts: list[str] = []
    if history_context:
        user_parts.append(f"PREVIOUS CONVERSATION:\n{history_context}\n")
    user_parts.append(f"USER QUESTION:\n{question}\n")
    user_parts.append("WIKI CONTEXT:")
    budget = max_total_chars - sum(len(p) for p in user_parts)
    for name, body in context_chunks:
        chunk = f"\n--- {name} ---\n{body[:max_body]}"
        if len(chunk) > budget:
            if budget > 200:
                user_parts.append(chunk[:budget])
            break
        user_parts.append(chunk)
        budget -= len(chunk)
    return "\n".join(user_parts)


def answer_query(
    config: Config,
    *,
    question: str,
    quick: bool = False,
    deep: bool = False,
    history_context: str = "",
) -> QueryResult:
    """Run tiered retrieval + synthesis. Returns the full answer non-streaming.

    Reused by both `run_query` (CLI) and `ops/serve.py` (HTTP).
    """
    vault = _get_vault(config)
    prompts = PromptLoader(
        skills_dir=config.settings.skills_dir,
        schema_dir=Path(__file__).resolve().parents[2] / "docs" / "Schema",
    )
    manifest = _get_manifest(config)
    with Ollama(
        host=config.settings.ollama_host,
        model=config.settings.model_for_query,
        timeout=config.settings.ollama_timeout,
    ) as ollama:
        ctx = QueryContext(config=config, vault=vault, ollama=ollama, prompts=prompts, manifest=manifest)
        if not ctx.ollama.ping():
            raise OllamaError(f"Ollama not reachable at {config.settings.ollama_host}")

        import time as _time

        retrieval_question = _expand_followup(question, history_context)
        if retrieval_question != question:
            log.info("follow-up expanded: %r → %r", question[:60], retrieval_question[:60])

        mode = config.settings.retrieval_mode
        idx_text = (ctx.vault.wiki / "index.md").read_text(encoding="utf-8") if (ctx.vault.wiki / "index.md").exists() else ""

        t_ret = _time.monotonic()
        if quick:
            context_chunks = [("Wiki/index.md", idx_text)]
        elif mode == "adaptive":
            context_chunks = _adaptive_retrieve(retrieval_question, ctx, idx_text, deep=deep)
        else:
            context_chunks = _gather_context(
                retrieval_question, ctx, deep=deep, idx_text=idx_text,
                use_vectors=mode in ("hybrid", "reranked"),
                use_rerank=mode == "reranked",
            )
        retrieval_ms = (_time.monotonic() - t_ret) * 1000
        cited = [name for name, _ in context_chunks]
        context_chars = sum(len(body) for _, body in context_chunks)
        log.info("retrieval: mode=%s, %d pages, %d chars, %.0fms", mode, len(cited), context_chars, retrieval_ms)

        system = ctx.prompts.system_for_query()
        max_chars = config.settings.num_ctx_query * 3
        user = _build_user_prompt(question, context_chunks, history_context=history_context, max_body=config.settings.max_body_query, max_total_chars=max_chars)
        prompt_chars = len(system) + len(user)

        t_llm = _time.monotonic()
        answer = ctx.ollama.chat(system, user, temperature=0.2, num_ctx=config.settings.num_ctx_query)
        llm_ms = (_time.monotonic() - t_llm) * 1000
        metrics = ctx.ollama.last_metrics
        log.info(
            "query: %r — retrieval=%.0fms, llm=%.0fms (%.0f tok/s), prompt=%d chars, answer=%d chars, cited=%d pages",
            question[:60], retrieval_ms, llm_ms,
            metrics.eval_rate if metrics else 0,
            prompt_chars, len(answer), len(cited),
        )

    # Brain: strengthen memories that were accessed (cited in this answer)
    _track_access(ctx.vault, cited)

    # Log query metrics
    try:
        manifest.log_query(
            question=question,
            cited_pages=cited,
            eval_rate=metrics.eval_rate if metrics else 0,
            duration_ms=metrics.total_duration_ms if metrics else 0,
            retrieval_mode=config.settings.retrieval_mode,
        )
    except Exception:  # noqa: BLE001
        pass

    return QueryResult(question=question, answer=answer, cited_pages=cited, history_context=history_context)


def stream_query(
    config: Config,
    *,
    question: str,
    quick: bool = False,
    deep: bool = False,
    history_context: str = "",
) -> tuple[list[str], Iterator[str]]:
    """Same as answer_query but streams. Returns (cited_pages, chunk_iterator).

    The cited_pages are computed before any token is yielded so the server can include them
    in the first SSE frame or in trailers.
    """
    vault = _get_vault(config)
    prompts = PromptLoader(
        skills_dir=config.settings.skills_dir,
        schema_dir=Path(__file__).resolve().parents[2] / "docs" / "Schema",
    )
    manifest = _get_manifest(config)

    ollama = Ollama(
        host=config.settings.ollama_host,
        model=config.settings.model_for_query,
        timeout=config.settings.ollama_timeout,
    )
    if not ollama.ping():
        ollama.close()
        raise OllamaError(f"Ollama not reachable at {config.settings.ollama_host}")

    ctx = QueryContext(config=config, vault=vault, ollama=ollama, prompts=prompts, manifest=manifest)

    import time as _time

    retrieval_question = _expand_followup(question, history_context)

    mode = config.settings.retrieval_mode
    idx_text = (ctx.vault.wiki / "index.md").read_text(encoding="utf-8") if (ctx.vault.wiki / "index.md").exists() else ""

    t_ret = _time.monotonic()
    if quick:
        context_chunks = [("Wiki/index.md", idx_text)]
    elif mode == "adaptive":
        context_chunks = _adaptive_retrieve(retrieval_question, ctx, idx_text, deep=deep)
    else:
        context_chunks = _gather_context(
            retrieval_question, ctx, deep=deep, idx_text=idx_text,
            use_vectors=mode in ("hybrid", "reranked"),
            use_rerank=mode == "reranked",
        )
    retrieval_ms = (_time.monotonic() - t_ret) * 1000
    cited = [name for name, _ in context_chunks]
    system = ctx.prompts.system_for_query()
    max_chars = config.settings.num_ctx_query * 3
    user = _build_user_prompt(question, context_chunks, history_context=history_context, max_body=config.settings.max_body_query, max_total_chars=max_chars)
    prompt_chars = len(system) + len(user)
    log.info("stream: retrieval=%.0fms, %d pages, prompt=%d chars (sys=%d + user=%d), num_ctx=%d",
             retrieval_ms, len(cited), prompt_chars, len(system), len(user), config.settings.num_ctx_query)

    def _generate() -> Iterator[str]:
        t0 = _time.monotonic()
        try:
            yield from ctx.ollama.chat_stream(system, user, temperature=0.2, num_ctx=config.settings.num_ctx_query)
        finally:
            llm_ms = (_time.monotonic() - t0) * 1000
            log.info("stream: llm=%.1fs, question=%r", llm_ms / 1000, question[:60])
            try:
                manifest.log_query(
                    question=question, cited_pages=cited,
                    eval_rate=0, duration_ms=retrieval_ms + llm_ms,
                    retrieval_mode=mode,
                )
            except Exception:  # noqa: BLE001
                pass
            ctx.ollama.close()

    return cited, _generate()


def run_query(
    config: Config,
    *,
    question: str | None,
    explain: str | None,
    quick: bool,
    deep: bool,
    cite_only: bool,
    json_out: bool,
    console: Console,
) -> None:
    vault = _get_vault(config)
    resolved = _resolve_question(vault, question, explain)
    if resolved is None:
        if explain:
            console.print(f"[red]No such page: {explain}[/red]")
        else:
            console.print("[yellow]No question to ask.[/yellow]")
        return

    if cite_only:
        # Replicate Tier-1+ retrieval to print citations without calling the LLM.
        # Build a minimal QueryContext — ollama won't be used (keyword-only).
        prompts = PromptLoader(
            skills_dir=config.settings.skills_dir,
            schema_dir=Path(__file__).resolve().parents[2] / "docs" / "Schema",
        )
        manifest = _get_manifest(config)
        with Ollama(
            host=config.settings.ollama_host,
            model=config.settings.model_for_query,
            timeout=config.settings.ollama_timeout,
        ) as ollama:
            ctx = QueryContext(config=config, vault=vault, ollama=ollama, prompts=prompts, manifest=manifest)
            idx_text = (vault.wiki / "index.md").read_text(encoding="utf-8") if (vault.wiki / "index.md").exists() else ""
            context_chunks = _gather_context(resolved, ctx, deep=deep, idx_text=idx_text)
        console.print("[bold]Cited pages[/bold]")
        for name, _ in context_chunks:
            console.print(f"  - {name}")
        return

    try:
        result = answer_query(config, question=resolved, quick=quick, deep=deep)
    except OllamaError as e:
        console.print(f"[red]Ollama query failed: {e}[/red]")
        return

    if json_out:
        print(json.dumps(
            {"question": result.question, "answer": result.answer, "context": result.cited_pages},
            indent=2,
        ))
        return
    console.print(result.answer)


def _track_access(vault: Vault, cited_paths: list[str]) -> None:
    """Increment access_count on pages cited in a query — strengthens their memory."""
    now = datetime.now().isoformat(timespec="seconds")
    for rel in cited_paths:
        if rel == "Wiki/index.md":
            continue
        try:
            path = vault.root / rel
            if not path.exists():
                continue
            page = vault.read_page(path)
            if page.kind in ("doc", "source-summary"):
                continue
            page.frontmatter["access_count"] = int(page.frontmatter.get("access_count", 0)) + 1
            page.frontmatter["last_accessed"] = now
            vault.write_page(page)
        except Exception:  # noqa: BLE001
            pass

