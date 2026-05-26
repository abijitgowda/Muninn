"""ChromaDB-backed vector store for semantic memory.

Embeds page summaries once on ingest, re-embeds only on change. Queries hit
pre-computed vectors — 1 embedding call per query regardless of vault size.

Storage: <vault>/.muninn/chroma/ (persistent, survives restarts)
Model: mxbai-embed-large via Ollama (same model Copilot uses)

Designed for infinite scale — ChromaDB handles 100K+ documents on local disk.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    _HAS_CHROMA = True
except ImportError:
    _HAS_CHROMA = False


class VectorStore:
    """Persistent local vector store backed by ChromaDB + Ollama embeddings."""

    COLLECTION_NAME = "wiki_pages"

    def __init__(
        self,
        persist_dir: Path,
        ollama_host: str = "http://localhost:11434",
        embed_model: str = "mxbai-embed-large",
    ) -> None:
        if not _HAS_CHROMA:
            raise ImportError(
                "chromadb is not installed. Run: pip install chromadb"
            )
        self.persist_dir = persist_dir
        self.ollama_host = ollama_host
        self.embed_model = embed_model

        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._embed_fn = _OllamaEmbedFn(ollama_host, embed_model)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        log.debug(
            "VectorStore: %d documents in %s",
            self._collection.count(),
            persist_dir,
        )

    @property
    def count(self) -> int:
        return self._collection.count()

    @property
    def embed_metrics(self) -> EmbedMetrics:
        return self._embed_fn.metrics

    # ---- write (called during ingest) ----

    def upsert_page(
        self,
        page_id: str,
        title: str,
        summary: str,
        kind: str,
        schema: str = "",
        strength: float = 1.0,
        tags: list[str] | None = None,
    ) -> None:
        """Embed and store a page. Re-embeds only if content changed."""
        doc_text = f"{title}. {summary}"
        content_hash = hashlib.sha256(doc_text.encode()).hexdigest()[:32]

        existing = self._collection.get(ids=[page_id], include=["metadatas"])
        if existing["ids"] and existing["metadatas"]:
            old_hash = (existing["metadatas"][0] or {}).get("content_hash")
            if old_hash == content_hash:
                # Content unchanged — update metadata only (no re-embedding)
                self._collection.update(
                    ids=[page_id],
                    metadatas=[{
                        "title": title,
                        "kind": kind,
                        "schema": schema,
                        "strength": strength,
                        "content_hash": content_hash,
                        "tags": ",".join(tags or []),
                    }],
                )
                return

        self._collection.upsert(
            ids=[page_id],
            documents=[doc_text],
            metadatas=[{
                "title": title,
                "kind": kind,
                "schema": schema,
                "strength": strength,
                "content_hash": content_hash,
                "tags": ",".join(tags or []),
            }],
        )

    def delete_page(self, page_id: str) -> None:
        try:
            self._collection.delete(ids=[page_id])
        except Exception:  # noqa: BLE001
            pass

    # ---- read (called during query) ----

    def query(
        self,
        question: str,
        n_results: int = 15,
        where_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search. Returns [{id, title, kind, schema, strength, distance}]."""
        kwargs: dict[str, Any] = {
            "query_texts": [question],
            "n_results": min(n_results, self._collection.count() or 1),
            "include": ["metadatas", "distances"],
        }
        if where_filter:
            kwargs["where"] = where_filter

        try:
            results = self._collection.query(**kwargs)
        except Exception as e:  # noqa: BLE001
            log.warning("VectorStore query failed: %s", e)
            return []

        out: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for page_id, meta, dist in zip(ids, metas, dists, strict=False):
            out.append({
                "id": page_id,
                "title": (meta or {}).get("title", ""),
                "kind": (meta or {}).get("kind", ""),
                "schema": (meta or {}).get("schema", ""),
                "strength": float((meta or {}).get("strength", 1.0)),
                "distance": float(dist),
                "similarity": 1.0 - float(dist),
            })
        return out

    # ---- maintenance ----

    def sync_vault(self, pages: list[Any]) -> tuple[int, int]:
        """Sync the vector store with the current vault state.
        Returns (upserted, deleted).
        """
        current_ids = set()
        upserted = 0
        for p in pages:
            if p.kind in ("doc", "source-summary", "redirect"):
                continue
            page_id = p.path.stem
            current_ids.add(page_id)
            self.upsert_page(
                page_id=page_id,
                title=p.frontmatter.get("title", p.path.stem),
                summary=p.frontmatter.get("summary", ""),
                kind=p.kind,
                schema=p.frontmatter.get("schema", ""),
                strength=float(p.frontmatter.get("strength", 1.0)),
                tags=p.frontmatter.get("tags"),
            )
            upserted += 1

        # Remove vectors for deleted pages
        stored = set(self._collection.get(include=[])["ids"])
        to_delete = stored - current_ids
        deleted = 0
        for pid in to_delete:
            self.delete_page(pid)
            deleted += 1

        return upserted, deleted


class EmbedMetrics:
    """Cumulative embedding performance metrics."""

    def __init__(self) -> None:
        self.total_texts: int = 0
        self.total_calls: int = 0
        self.total_ms: float = 0

    @property
    def avg_ms_per_text(self) -> float:
        return self.total_ms / self.total_texts if self.total_texts else 0

    @property
    def texts_per_sec(self) -> float:
        return (self.total_texts / (self.total_ms / 1000)) if self.total_ms else 0

    def __str__(self) -> str:
        if not self.total_texts:
            return "no embeddings yet"
        return f"{self.texts_per_sec:.0f} texts/s ({self.total_texts} texts in {self.total_calls} calls, {self.total_ms/1000:.1f}s)"


class _OllamaEmbedFn:
    """ChromaDB-compatible embedding function using Ollama's API."""

    def __init__(self, host: str, model: str) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self._name = f"ollama-{model}"
        self.metrics = EmbedMetrics()
        import httpx
        self._client = httpx.Client(timeout=120.0)

    def name(self) -> str:
        return self._name

    def embed_query(self, input):
        texts = [input] if isinstance(input, str) else list(input)
        return self(texts)

    def __call__(self, input) -> list[list[float]]:
        if isinstance(input, str):
            input = [input]
        import time
        all_embeddings: list[list[float]] = []
        batch_size = 50
        for i in range(0, len(input), batch_size):
            batch = input[i : i + batch_size]
            t0 = time.monotonic()
            r = self._client.post(
                f"{self.host}/v1/embeddings",
                json={"model": self.model, "input": batch},
            )
            r.raise_for_status()
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.metrics.total_texts += len(batch)
            self.metrics.total_calls += 1
            self.metrics.total_ms += elapsed_ms
            data = r.json().get("data") or []
            sorted_embs = [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
            all_embeddings.extend(sorted_embs)
        return all_embeddings
