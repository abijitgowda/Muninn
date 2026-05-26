---
name: muninn-source-connector
description: Create a new Muninn source connector that fetches data from an external system and yields RawItem objects for the ingest pipeline.
version: "1.0"
temperature: 0.0
---

Create a source connector for Muninn. Every connector is a Python file in `muninn/sources/` plus a YAML entry in `wiki.yaml`.

## Contract

Subclass `Source` from `muninn/sources/base.py` and implement:

```python
from muninn.sources.base import Source, RawItem

class MySource(Source):
    @property
    def source_type(self) -> str:
        return "my-type"  # appears in frontmatter as source_type

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        # since=None on first run, datetime on subsequent runs
        # Yield one RawItem per discrete unit (one article, one message, etc.)
        yield RawItem(
            id=self.hash_id(self.name, unique_key),
            title="...",
            url="https://...",
            timestamp=datetime.now(),
            body="full text content",
            extra_frontmatter={"custom_field": "value"},
        )
```

## Rules

- File goes in `muninn/sources/` with a descriptive name (e.g. `slack.py`, `reddit.py`)
- Class is registered in `wiki.yaml` via `tool: muninn.sources.my_source:MySource`
- `fetch()` must be idempotent: use `since` parameter to only return new items
- Use `self.hash_id(self.name, unique_key)` for deterministic item IDs
- Use `self.options` dict for user-configurable settings from wiki.yaml
- Use `self.secret` for API keys (resolved from `.env` via `secret_env:` in wiki.yaml)
- Call `self.validate_fetch_url(url)` before fetching external URLs (SSRF protection)
- Body should be clean text, not HTML. Use `trafilatura` or similar for web content
- Keep dependencies minimal. Add any new packages to `pyproject.toml` under `[project.optional-dependencies]`

## wiki.yaml entry

```yaml
- name: my-source
  type: my-type
  enabled: true
  url: "https://api.example.com"
  secret_env: MY_API_KEY        # optional, reads from .env
  tool: muninn.sources.my_source:MySource
  options:
    max_items: 100
    custom_option: value
```

## Reference connectors

Study these for patterns:
- `browser_history.py` — reads local SQLite, fetches web content
- `x.py` — REST API with bearer token auth
- `folder.py` — local filesystem walker with extension filtering
- `inbox.py` — vault folder watcher, simplest connector
- `youtube.py` — extracts transcripts from video URLs
