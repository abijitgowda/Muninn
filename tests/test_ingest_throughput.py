"""Ingest throughput features: thinking control, JSON robustness, exclusions, parallel extraction."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import httpx
import pytest
import yaml
from rich.console import Console

from muninn.config import Settings, SourceConfig, load_config
from muninn.llm import create_provider
from muninn.llm.base import LLMMetrics, LLMProvider, parse_json_object
from muninn.ollama import Ollama, OllamaError
from muninn.pipeline import Pipeline
from muninn.sources.browser_history import BrowserHistorySource
from muninn.sources.folder import FolderSource

# ---- JSON parsing ----


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '<think>\nlet me consider {"draft": true}\n</think>\n{"a": 1}',
        'Here is the JSON:\n{"a": 1}\nHope that helps.',
    ],
)
def test_parse_json_object_tolerates_wrappers(raw):
    assert parse_json_object(raw) == {"a": 1}


def test_parse_json_object_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        parse_json_object("no json here")


# ---- Ollama request shape ----


def _ollama_with(handler, **kw) -> Ollama:
    client = Ollama(host="http://ollama", model="m", **kw)
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama")
    return client


def _ok(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"message": {"content": content}, "eval_count": 3, "eval_duration": 10**9}
    )


def test_ollama_sends_think_and_keep_alive():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok('{"ok": true}')

    client = _ollama_with(handler, think=False, keep_alive="1h")
    assert client.chat_json("s", "u") == {"ok": True}
    assert seen[0]["think"] is False
    assert seen[0]["keep_alive"] == "1h"


def test_ollama_omits_think_when_unset():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok("{}")

    _ollama_with(handler).chat_json("s", "u")
    assert "think" not in seen[0] and "keep_alive" not in seen[0]


def test_ollama_drops_think_for_models_without_thinking():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": '"m" does not support thinking'})
        return _ok('{"ok": 1}')

    client = _ollama_with(handler, think=False)
    assert client.chat_json("s", "u") == {"ok": 1}
    assert client._think_unsupported  # remembered for later calls
    client.chat_json("s", "u")
    assert "think" not in seen[-1]


def test_ollama_regenerates_once_on_invalid_json():
    replies = iter(['{"truncated": ', '{"ok": 2}'])
    temps = []

    def handler(req):
        temps.append(json.loads(req.content)["options"]["temperature"])
        return _ok(next(replies))

    assert _ollama_with(handler).chat_json("s", "u") == {"ok": 2}
    assert temps == [0.0, 0.3]


def test_ollama_invalid_json_twice_raises():
    with pytest.raises(OllamaError, match="invalid JSON"):
        _ollama_with(lambda req: _ok("nope")).chat_json("s", "u")


# ---- OpenAI-compatible thinking control ----


def _capture_openai(provider):
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"x": 1}'}}], "usage": {}})

    provider._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://srv/v1")
    return seen


def test_lmstudio_and_custom_send_enable_thinking():
    for llm in (
        create_provider("lmstudio", think=False),
        create_provider("custom", base_url="http://localhost:8000/v1", think=False),
    ):
        seen = _capture_openai(llm)
        assert llm.chat_json("s", "u") == {"x": 1}
        assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_hosted_openai_never_sends_template_kwargs():
    llm = create_provider("openai", api_key="k", think=False)
    seen = _capture_openai(llm)
    llm.chat_json("s", "u")
    assert "chat_template_kwargs" not in seen[0]


# ---- config ----


def _settings(tmp_path, settings: dict) -> Settings:
    cfg = tmp_path / "wiki.yaml"
    cfg.write_text(yaml.safe_dump({"settings": settings, "sources": []}))
    (tmp_path / "vault").mkdir(exist_ok=True)
    return load_config(config_path=cfg, vault_path=tmp_path / "vault").settings


def test_thinking_defaults_off_for_ingest_only(tmp_path):
    s = _settings(tmp_path, {})
    assert s.ingest_thinking is False and s.query_thinking is None
    assert s.ingest_concurrency == 1
    with s.create_llm("ingest") as ingest, s.create_llm("query") as query:
        assert ingest.think is False
        assert query.think is None


def test_throughput_settings_parse(tmp_path):
    s = _settings(
        tmp_path,
        {"ingest_thinking": True, "query_thinking": False, "ingest_concurrency": 4, "ollama_keep_alive": -1},
    )
    assert s.ingest_thinking is True and s.query_thinking is False
    assert s.ingest_concurrency == 4
    assert s.ollama_keep_alive == -1  # numeric: Ollama rejects the string "-1"
    with s.create_llm("ingest") as llm:
        assert llm._client.keep_alive == -1
    assert _settings(tmp_path, {"ollama_keep_alive": "1h"}).ollama_keep_alive == "1h"
    assert _settings(tmp_path, {"ollama_keep_alive": "-1"}).ollama_keep_alive == -1


# ---- exclusions for already-queued items ----


def test_folder_is_excluded_by_any_path_component():
    src = FolderSource(name="docs", url="/x", secret=None, options={"exclude_paths": ["Alaya-Vault", "Pics"]})
    assert src.is_excluded({"relative_folder": "Alaya-Vault/Wiki/Concepts"})
    assert src.is_excluded({"relative_folder": "Family/pics/2019"})
    assert not src.is_excluded({"relative_folder": "Immigration Docs/2022"})
    assert not src.is_excluded({})


def test_browser_history_is_excluded_by_domain_suffix():
    src = BrowserHistorySource(
        name="edge", url="", secret=None, options={"exclude_domains": ["atlassian.net"]}
    )
    assert src.is_excluded({"source_url": "https://team.atlassian.net/wiki/x"})
    assert src.is_excluded({"source_url": "https://www.google.com/search?q=x"})  # default list
    assert not src.is_excluded({"source_url": "https://notatlassian.net/x"})
    assert not src.is_excluded({"source_url": "https://substack.com/p/x"})


# ---- parallel extraction through the pipeline ----


class FakeLLM(LLMProvider):
    """Returns one entity per item; tracks peak concurrency across instances."""

    lock = threading.Lock()
    active = 0
    peak = 0
    instances = 0

    def __init__(self, fail_on: str | None = None):
        self.model = "fake"
        self.last_metrics = LLMMetrics()
        self.fail_on = fail_on
        with FakeLLM.lock:
            FakeLLM.instances += 1

    def ping(self):
        return True

    def chat(self, *a, **k):
        raise NotImplementedError

    def chat_stream(self, *a, **k):
        raise NotImplementedError

    def embed(self, texts, *, model=None):
        return []

    def chat_json(self, system, user, **kw):
        with FakeLLM.lock:
            FakeLLM.active += 1
            FakeLLM.peak = max(FakeLLM.peak, FakeLLM.active)
        try:
            time.sleep(0.05)
            topic = user.split("=== BODY ===\n", 1)[1].split()[1]  # "About <topic> ..."
            if self.fail_on and self.fail_on == topic:
                raise RuntimeError("boom")
            self.last_metrics = LLMMetrics(model="fake", completion_tokens=10, eval_rate=100.0)
            return {
                "summary": f"About {topic}",
                "entities": [{"name": f"Entity {topic}", "kind": "org", "context": f"{topic} context."}],
                "concepts": [],
                "topics": [],
                "relationships": [],
                "claims": [],
                "log_summary": topic,
                "page_body": f"Body about {topic}.",
            }
        finally:
            with FakeLLM.lock:
                FakeLLM.active -= 1


@pytest.fixture
def pipeline_factory(tmp_path, monkeypatch):
    def make(concurrency: int, n_items: int = 6, fail_on: str | None = None, exclude=None):
        FakeLLM.active = FakeLLM.peak = FakeLLM.instances = 0
        monkeypatch.setattr(Settings, "create_llm", lambda self, op="query": FakeLLM(fail_on))
        vault = tmp_path / f"vault{concurrency}{fail_on}"
        (vault / "Wiki").mkdir(parents=True)
        settings = Settings(vault_path=vault, two_pass_ingest=False, ingest_concurrency=concurrency)
        src_cfg = SourceConfig(
            name="docs",
            type="folder",
            url=str(tmp_path),
            tool="muninn.sources.folder:FolderSource",
            options={"exclude_paths": exclude or []},
        )
        from muninn.config import Config

        pipe = Pipeline(Config(settings=settings, sources=[src_cfg]), console=Console(quiet=True))
        for i in range(n_items):
            folder = "Junk" if i == 0 else "Notes"
            md = (
                f"---\nsource_type: folder\ntitle: Note {i}\nrelative_folder: {folder}\n---\n\n"
                f"About topic{i} with enough words to be a real document body for ingest."
            )
            raw = pipe.vault.write_raw("docs", f"id{i}", md, date=datetime(2026, 1, 1))
            pipe.manifest.add_item("docs", f"id{i}", str(raw.relative_to(vault)))
        return pipe, src_cfg

    return make


def _run_pending(pipe, src_cfg):
    from muninn.pipeline import instantiate_source

    source = instantiate_source(src_cfg)
    items = [
        it for it in pipe.manifest.pending("docs") if not pipe._screen(src_cfg, source, it.path, it.item_id)
    ]
    return pipe._process_pending(src_cfg, items)


def test_parallel_ingest_processes_everything_concurrently(pipeline_factory):
    pipe, src_cfg = pipeline_factory(concurrency=3)
    results = _run_pending(pipe, src_cfg)
    assert sorted(r.item_id for r in results if r.status == "processed") == [f"id{i}" for i in range(6)]
    assert FakeLLM.peak >= 2  # really overlapped
    assert FakeLLM.instances <= 1 + 3  # pipeline's own client + one per worker
    assert not list(pipe.manifest.pending("docs"))
    for i in range(6):
        assert pipe.vault.find_page(f"Entity topic{i}") is not None


def test_sequential_and_parallel_write_same_pages(pipeline_factory):
    seq, cfg1 = pipeline_factory(concurrency=1)
    _run_pending(seq, cfg1)
    assert FakeLLM.peak == 1
    par, cfg2 = pipeline_factory(concurrency=4)
    _run_pending(par, cfg2)

    def pages(p):
        return sorted(x.relative_to(p.vault.wiki).as_posix() for x in p.vault.wiki.rglob("*.md"))

    assert pages(seq) == pages(par)


def test_parallel_failure_marks_only_that_item(pipeline_factory):
    pipe, src_cfg = pipeline_factory(concurrency=3, fail_on="topic2")
    results = {r.item_id: r.status for r in _run_pending(pipe, src_cfg)}
    assert results["id2"] == "failed"
    assert sum(1 for s in results.values() if s == "processed") == 5
    assert pipe.manifest.get_item("docs", "id2").status == "failed"


def test_screen_skips_excluded_queued_items(pipeline_factory):
    pipe, src_cfg = pipeline_factory(concurrency=2, exclude=["Junk"])
    results = _run_pending(pipe, src_cfg)
    assert "id0" not in {r.item_id for r in results}
    item = pipe.manifest.get_item("docs", "id0")
    assert item.status == "skipped" and item.error == "excluded by config"


# ---- per-request thinking on LM Studio (assistant pre-fill) ----


def _lmstudio(model="qwen3.5-122b-a10b", think=None, reasoning=""):
    llm = create_provider("lmstudio", model=model, think=think)
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        msg = {"content": '{"x": 1}', "reasoning_content": reasoning}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {}})

    llm._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://srv/v1")
    return llm, seen


def test_lmstudio_think_off_prefills_empty_think_block():
    from muninn.llm.lmstudio_provider import EMPTY_THINK_PREFILL

    llm, seen = _lmstudio(think=False)
    llm.chat_json("s", "u")
    assert seen[0]["messages"][-1] == {"role": "assistant", "content": EMPTY_THINK_PREFILL}


def test_lmstudio_think_on_or_default_does_not_prefill():
    for think in (True, None):
        llm, seen = _lmstudio(think=think)
        llm.chat("s", "u")
        assert seen[0]["messages"][-1]["role"] == "user"


def test_lmstudio_prefill_only_for_think_tag_models():
    llm, seen = _lmstudio(model="gemma-4-31b", think=False)
    llm.chat("s", "u")
    assert seen[0]["messages"][-1]["role"] == "user"


def test_per_call_think_overrides_provider_setting():
    llm, seen = _lmstudio(think=True)  # e.g. the query model
    llm.chat_json("s", "u", think=False)  # e.g. the reranker
    assert seen[-1]["messages"][-1]["role"] == "assistant"
    llm.chat("s", "u")  # back to the provider default
    assert seen[-1]["messages"][-1]["role"] == "user"

    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return _ok("{}")

    client = _ollama_with(handler, think=None)
    client.chat_json("s", "u", think=False)
    client.chat_json("s", "u")
    assert sent[0]["think"] is False and "think" not in sent[1]


def test_warns_once_when_reasoning_comes_back_with_thinking_off(caplog):
    llm, _ = _lmstudio(think=False, reasoning="I should think about this")
    with caplog.at_level("WARNING"):
        llm.chat("s", "u")
        llm.chat("s", "u")
    assert sum("thinking is off" in r.message for r in caplog.records) == 1


def test_progress_summary_reports_aggregate_rate_and_eta():
    from muninn.pipeline import _progress_summary

    assert _progress_summary(1, 2000, 70, 945) == ""  # too early to estimate
    line = _progress_summary(20, 40000, 360, 945)  # 20 items in 6 min
    assert "total 111 tok/s" in line and "200 items/h" in line and "~4h37m left" in line
    assert _progress_summary(940, 10, 100, 945).endswith("~0m left")


# ---- operability: server error text, requeue ----


def test_lmstudio_error_includes_server_message():
    from muninn.llm.base import LLMError

    llm = create_provider("lmstudio", model="qwen3.5-122b-a10b")

    def handler(request):
        return httpx.Response(500, json={"error": "Model unloaded."})

    llm._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://srv/v1")
    with pytest.raises(LLMError, match=r"500 Internal Server Error: Model unloaded\."):
        llm.chat("s", "u", retries=0)


def test_requeue_failed(tmp_path):
    from muninn.manifest import Manifest

    mf = Manifest(tmp_path / "manifest.db")
    for src, iid in [("a", "1"), ("a", "2"), ("b", "3")]:
        mf.add_item(src, iid, f"Raw/{iid}.md")
        mf.mark_failed(src, iid, "boom")
    mf.add_item("a", "4", "Raw/4.md")
    mf.mark_processed("a", "4", [])

    assert mf.requeue_failed("a") == 2
    assert {i.item_id for i in mf.pending("a")} == {"1", "2"}
    assert mf.get_item("a", "1").error is None
    assert mf.requeue_failed() == 1
    assert mf.get_item("a", "4").status == "processed"


# ---- embeddings: LM Studio (OpenAI-compatible) and Ollama (native, keep_alive) ----


def _embed_fn(provider, keep_alive=None):
    from muninn.vectorstore import _EmbedFn

    fn = _EmbedFn("http://srv", "m", provider, keep_alive=keep_alive)
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        n = len(body["input"])
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[float(i)] for i in range(n)]})
        # OpenAI-compatible servers may return items out of order
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(n))]}
        )

    fn._client = httpx.Client(transport=httpx.MockTransport(handler))
    return fn, seen


def test_lmstudio_embeddings_use_openai_endpoint():
    fn, seen = _embed_fn("lmstudio")
    assert fn(["a", "b", "c"]) == [[0.0], [1.0], [2.0]]
    assert seen[0][0] == "/v1/embeddings" and "keep_alive" not in seen[0][1]
    assert fn.name() == "lmstudio-m"


def test_ollama_embeddings_send_keep_alive():
    fn, seen = _embed_fn("ollama", keep_alive=-1)
    assert fn(["a", "b"]) == [[0.0], [1.0]]
    assert seen[0] == ("/api/embed", {"model": "m", "input": ["a", "b"], "keep_alive": -1})
    fn2, seen2 = _embed_fn("ollama")
    fn2(["a"])
    assert "keep_alive" not in seen2[0][1]


# ---- recovery: wait out a crashed / reloading local model ----


def _scripted_lmstudio(monkeypatch, responses):
    """LM Studio provider whose server replies with `responses` in order (status, body|exc)."""
    import muninn.llm.openai_provider as op

    sleeps = []
    monkeypatch.setattr(op.time, "sleep", sleeps.append)
    llm = create_provider("lmstudio", model="qwen3.5-122b-a10b")
    calls = iter(responses)

    def handler(request):
        status, body = next(calls)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    llm._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://srv/v1")
    return llm, sleeps


_OK = (200, {"choices": [{"message": {"content": "fine"}}], "usage": {}})


def test_waits_out_model_crash_and_reload(monkeypatch):
    llm, sleeps = _scripted_lmstudio(
        monkeypatch,
        [
            (500, {"error": "The model has crashed without additional information."}),
            (400, {"error": "No models loaded. Please load a model in the developer page."}),
            (None, httpx.ConnectError("refused")),
            _OK,
        ],
    )
    assert llm.chat("s", "u") == "fine"
    assert sleeps == [5, 15, 30]


def test_gives_up_after_transient_waits(monkeypatch):
    from muninn.llm.base import LLMError
    from muninn.llm.openai_provider import TRANSIENT_RETRY_WAITS

    gone = (400, {"error": "No models loaded."})
    llm, sleeps = _scripted_lmstudio(monkeypatch, [gone] * (len(TRANSIENT_RETRY_WAITS) + 1))
    with pytest.raises(LLMError, match="No models loaded"):
        llm.chat("s", "u")
    assert sleeps == list(TRANSIENT_RETRY_WAITS)


def test_real_bad_request_is_not_waited_out(monkeypatch):
    from muninn.llm.base import LLMError

    bad = (400, {"error": "Invalid 'messages' field"})
    llm, sleeps = _scripted_lmstudio(monkeypatch, [bad] * 3)
    with pytest.raises(LLMError, match="Invalid 'messages'"):
        llm.chat("s", "u", retries=2)
    assert sleeps == [1, 2]


def test_timeout_and_retries_zero_fail_fast(monkeypatch):
    from muninn.llm.base import LLMError

    llm, sleeps = _scripted_lmstudio(monkeypatch, [(None, httpx.ReadTimeout("slow"))])
    with pytest.raises(LLMError):
        llm.chat("s", "u", retries=0)
    llm, sleeps2 = _scripted_lmstudio(monkeypatch, [(500, {"error": "boom"})])
    with pytest.raises(LLMError, match="500"):
        llm.chat("s", "u", retries=0)
    assert sleeps == sleeps2 == []


# ---- planned reloads (LM Studio crash avoidance) ----


def test_lmstudio_reload_model_unloads_only_that_model(monkeypatch):
    llm = create_provider("lmstudio", model="qwen3.5-122b-a10b")
    calls = []
    listing = {
        "models": [
            {"key": "qwen3.5-122b-a10b", "loaded_instances": [{"id": "qwen3.5-122b-a10b"}]},
            {
                "key": "text-embedding-qwen3-embedding-8b",
                "loaded_instances": [{"id": "text-embedding-qwen3-embedding-8b"}],
            },
        ]
    }

    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=listing)
        return httpx.Response(200, json={"status": "ok"})

    real_client = httpx.Client
    monkeypatch.setattr(
        "muninn.llm.lmstudio_provider.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    llm.reload_model(context_length=131072)
    assert calls == [
        ("GET", "/api/v1/models", None),
        ("POST", "/api/v1/models/unload", {"instance_id": "qwen3.5-122b-a10b"}),
        ("POST", "/api/v1/models/load", {"model": "qwen3.5-122b-a10b", "context_length": 131072}),
    ]


def test_lmstudio_reload_failure_is_llm_error(monkeypatch):
    from muninn.llm.base import LLMError

    llm = create_provider("lmstudio", model="m")
    real_client = httpx.Client
    monkeypatch.setattr(
        "muninn.llm.lmstudio_provider.httpx.Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "nope"})), **kw
        ),
    )
    with pytest.raises(LLMError, match="reload of m failed"):
        llm.reload_model()


def _with_reload(pipe, every):
    reloads = []

    def reload_model(context_length=None):
        reloads.append(FakeLLM.active)  # requests in flight at reload time
        return 0.0

    pipe.llm.reload_model = reload_model
    pipe.config.settings.lmstudio_reload_every = every
    return reloads


@pytest.mark.parametrize("concurrency", [1, 3])
def test_pipeline_reloads_every_n_items_with_nothing_in_flight(pipeline_factory, concurrency):
    pipe, src_cfg = pipeline_factory(concurrency=concurrency, n_items=10)
    reloads = _with_reload(pipe, every=4)
    results = _run_pending(pipe, src_cfg)
    assert sum(r.status == "processed" for r in results) == 10
    assert reloads == [0, 0]  # after items 4 and 8; none after the last batch
    if concurrency > 1:
        assert FakeLLM.peak >= 2  # still batching between reloads


def test_pipeline_no_reload_when_disabled_or_unsupported(pipeline_factory):
    pipe, src_cfg = pipeline_factory(concurrency=3, n_items=10)
    reloads = _with_reload(pipe, every=0)
    _run_pending(pipe, src_cfg)
    assert reloads == []
    pipe2, cfg2 = pipeline_factory(concurrency=2, n_items=4)
    pipe2.config.settings.lmstudio_reload_every = 2  # FakeLLM has no reload_model
    assert sum(r.status == "processed" for r in _run_pending(pipe2, cfg2)) == 4


def test_config_parses_lmstudio_reload_every(tmp_path):
    cfg = tmp_path / "wiki.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"settings": {"vault_path": str(tmp_path), "lmstudio_reload_every": 50}, "sources": []}
        )
    )
    assert load_config(cfg, env_path=tmp_path / ".env").settings.lmstudio_reload_every == 50
    cfg.write_text(yaml.safe_dump({"settings": {"vault_path": str(tmp_path)}, "sources": []}))
    assert load_config(cfg, env_path=tmp_path / ".env").settings.lmstudio_reload_every == 0
