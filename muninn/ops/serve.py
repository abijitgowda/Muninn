"""`muninn serve` — local HTTP API.

Two surfaces:

  /v1/chat/completions      OpenAI-compat. Point Copilot / Cline / any OpenAI client here.
  /v1/models                OpenAI-compat. Returns the single virtual model "muninn".
  /query                    Native. Returns {answer, citations[], question}.
  /health                   Liveness check.

Listens on 127.0.0.1 by default. Optionally token-protected via MUNINN_API_TOKEN env.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

from rich.console import Console

from ..config import load_config
from ..ollama import OllamaError
from .query import answer_query, stream_query

# Pydantic body models live at module scope so FastAPI's introspection recognizes them
# as request-body params (models inside the route-defining function are not properly
# detected by FastAPI when build_app() is called lazily).
try:
    from pydantic import BaseModel, Field, field_validator

    def _flatten_content(value: Any) -> str:
        """OpenAI clients sometimes send `content` as a list of {type, text} parts
        (vision / structured prompts). Flatten any list shape to a plain string so
        downstream code stays simple. Strings pass through unchanged."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            out: list[str] = []
            for part in value:
                if isinstance(part, str):
                    out.append(part)
                elif isinstance(part, dict):
                    # Common shapes: {"type":"text","text":"..."}, {"type":"image_url",...}
                    t = part.get("text") or part.get("content") or ""
                    if t:
                        out.append(str(t))
                else:
                    out.append(str(part))
            return "\n".join(out)
        return str(value)

    class ChatMessage(BaseModel):
        role: str
        content: str = ""
        # Tool-call / function-call shapes from OpenAI clients; we accept and ignore.
        name: str | None = None
        tool_calls: list[Any] | None = None
        tool_call_id: str | None = None

        model_config = {"extra": "ignore"}

        @field_validator("content", mode="before")
        @classmethod
        def _coerce_content(cls, v: Any) -> str:
            return _flatten_content(v)

    class ChatCompletionRequest(BaseModel):
        # All fields lenient: every common OpenAI SDK field is accepted (or ignored)
        # so Copilot / Cline / generic clients don't get a 422 for sending optional knobs.
        model: str = "muninn"
        messages: list[ChatMessage] = Field(default_factory=list)
        stream: bool = False
        temperature: float | None = None
        muninn_quick: bool = Field(default=False, alias="muninn-quick")
        muninn_deep: bool = Field(default=False, alias="muninn-deep")

        model_config = {"populate_by_name": True, "extra": "ignore"}

    class QueryRequest(BaseModel):
        question: str
        quick: bool = False
        deep: bool = False

        model_config = {"extra": "ignore"}
except ImportError:
    # Pydantic missing → fastapi extras not installed. Server can't run.
    pass


def run_serve(
    *,
    host: str = "127.0.0.1",
    port: int = 19828,
    reload: bool = False,
    console: Console | None = None,
) -> None:
    console = console or Console()
    try:
        import uvicorn  # noqa: F401
    except ImportError as e:
        console.print(
            "[red]fastapi / uvicorn not installed.[/red] "
            "Run: [cyan]pip install -e '.[serve]'[/cyan] (or `uv sync --extra serve`)."
        )
        raise SystemExit(1) from e

    app = build_app()
    cfg = load_config()
    console.print(f"[bold green]Muninn serve[/bold green]  http://{host}:{port}")
    console.print(
        f"  Models — query: [cyan]{cfg.settings.model_for_query}[/cyan]  ingest: [cyan]{cfg.settings.model_for_ingest}[/cyan]"
    )
    console.print(f"  OpenAI-compat:  http://{host}:{port}/v1/chat/completions")
    console.print(f"  Native query:   http://{host}:{port}/query")
    console.print(f"  Health:         http://{host}:{port}/health")
    if os.environ.get("MUNINN_API_TOKEN"):
        console.print("  [yellow]auth: bearer token required (MUNINN_API_TOKEN)[/yellow]")
    else:
        console.print("  auth: none (localhost-only)")

    import uvicorn

    log_dir = cfg.settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / "serve.log")
    console.print(f"  Log: [cyan]{log_file}[/cyan]")

    import logging

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger("muninn").addHandler(file_handler)

    # Pre-warm caches so first query is fast
    import time as _time

    t0 = _time.monotonic()
    from .query import _get_vault
    from .retrieval import _get_vectorstore

    vault = _get_vault(cfg)
    vault._ensure_slug_index()
    vault.all_pages()
    _get_vectorstore(vault, cfg.settings.ollama_host)
    console.print(
        f"  Cache warmed: {len(vault._slug_index or {})} pages in {(_time.monotonic() - t0) * 1000:.0f}ms"
    )

    uvicorn.run(app, host=host, port=port, reload=reload, log_level="info")


def build_app() -> Any:
    """Construct the FastAPI app. Defined as a function so deps are imported lazily."""
    import hmac
    import logging

    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, StreamingResponse

    log = logging.getLogger("muninn.serve")
    app = FastAPI(title="Muninn", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def _log_validation_error(request: Request, exc: RequestValidationError):
        try:
            body = await request.body()
            body_preview = body.decode("utf-8", errors="replace")[:2000]
        except Exception:  # noqa: BLE001
            body_preview = "<unreadable>"
        log.warning(
            "422 on %s %s — errors=%s body=%s",
            request.method,
            request.url.path,
            exc.errors(),
            body_preview,
        )
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "received": body_preview[:500]},
        )

    config = load_config()

    AUTH_TOKEN = os.environ.get("MUNINN_API_TOKEN")  # noqa: N806

    # Obsidian plugins fetch from origin `app://obsidian.md`; browsers preflight every
    # cross-origin POST. Without CORS, the fetch fails with "Failed to fetch".
    # When auth is enabled, restrict origins; otherwise wildcard is safe on localhost.
    cors_origins = ["*"]
    if AUTH_TOKEN:
        cors_origins = ["app://obsidian.md", "http://localhost", "http://127.0.0.1"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def _check_auth(authorization: str | None) -> None:
        if not AUTH_TOKEN:
            return
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization.split(None, 1)[1].strip()
        if not hmac.compare_digest(token, AUTH_TOKEN):
            raise HTTPException(status_code=401, detail="invalid token")

    # ---- helpers ----

    def _flatten_history(messages: list[ChatMessage]) -> tuple[str, str]:
        """Return (latest_user_question, prior_conversation_as_text)."""
        if not messages:
            return "", ""
        # Latest user message → the question; anything before → history context.
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return "", ""
        question = messages[last_user_idx].content.strip()
        prior = messages[:last_user_idx]
        history = "\n".join(f"{m.role.upper()}: {m.content.strip()}" for m in prior if m.content.strip())
        return question, history

    def _completion_envelope(content: str, model: str = "muninn") -> dict[str, Any]:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _sse_chunk(content: str | None, model: str, finish: str | None = None) -> bytes:
        delta: dict[str, Any] = {}
        if content is not None:
            delta["content"] = content
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload)}\n\n".encode()

    # ---- routes ----

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "model_query": config.settings.model_for_query,
            "model_ingest": config.settings.model_for_ingest,
            "vault": str(config.settings.vault_path),
        }

    @app.get("/v1/models")
    def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _check_auth(authorization)
        return {
            "object": "list",
            "data": [
                {
                    "id": "muninn",
                    "object": "model",
                    "created": 0,
                    "owned_by": "muninn",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(
        req: ChatCompletionRequest,
        authorization: str | None = Header(default=None),
    ):
        _check_auth(authorization)
        if len(req.messages) > 100:
            raise HTTPException(status_code=400, detail="too many messages (max 100)")
        question, history = _flatten_history(req.messages)

        # Copilot meta-requests: title generation, conversation wrapping — skip full retrieval
        _meta_signals = ("<conversation_text>", "Generate a concise title", "Generate a title")
        if any(question.startswith(sig) for sig in _meta_signals):
            log.debug("skipping Copilot meta-request: %s", question[:40])
            if req.stream:

                def _empty():
                    yield _sse_chunk("", req.model, finish="stop")
                    yield b"data: [DONE]\n\n"

                return StreamingResponse(_empty(), media_type="text/event-stream")
            return JSONResponse(_completion_envelope("", model=req.model))

        log.info(
            "request: %d messages, stream=%s, question=%r, history=%d chars",
            len(req.messages),
            req.stream,
            question[:80],
            len(history),
        )
        if not question:
            raise HTTPException(status_code=400, detail="no user message provided")

        quick = req.muninn_quick
        deep = req.muninn_deep

        if not req.stream:
            import time as _time

            t0 = _time.monotonic()
            try:
                result = answer_query(
                    config,
                    question=question,
                    quick=quick,
                    deep=deep,
                    history_context=history,
                )
            except OllamaError as e:
                log.error("query failed: %s — %s", question[:80], e)
                raise HTTPException(status_code=503, detail=str(e)) from e
            elapsed = (_time.monotonic() - t0) * 1000
            log.info("query: %s — %d citations, %.0fms", question[:80], len(result.cited_pages), elapsed)
            envelope = _completion_envelope(result.answer, model=req.model)
            envelope["muninn_citations"] = result.cited_pages
            return JSONResponse(envelope)

        # Streaming path
        def _gen() -> Iterator[bytes]:
            try:
                cited, chunks = stream_query(
                    config,
                    question=question,
                    quick=quick,
                    deep=deep,
                    history_context=history,
                )
            except OllamaError as e:
                yield _sse_chunk(f"[error] {e}", req.model, finish="stop")
                yield b"data: [DONE]\n\n"
                return
            # Emit citations first as a hidden chunk inside content (clients ignore tags they don't parse).
            for chunk in chunks:
                yield _sse_chunk(chunk, req.model)
            yield _sse_chunk(None, req.model, finish="stop")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.post("/query")
    def native_query(
        body: QueryRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_auth(authorization)
        try:
            result = answer_query(config, question=body.question, quick=body.quick, deep=body.deep)
        except OllamaError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        return {
            "question": result.question,
            "answer": result.answer,
            "citations": result.cited_pages,
        }

    return app
