---
title: "04 — Add a new source"
kind: doc
tags: [tutorial]
lifecycle: pinned
---

# 04 — Add a new source

The connector model is: **one Python file + one YAML entry**. Adding a Pocket reading list, a Slack channel, a Notion database, or a YouTube watch history follows the same pattern.

## The contract

Every connector implements `muninn.sources.base.Source`:

```python
class Source(Protocol):
    name: str         # unique config name
    type: str         # the connector kind
    url: str          # how to reach the data (file path or HTTP URL)
    secret: str | None    # resolved from .env via secret_env
    options: dict     # connector-specific config

    def fetch(self, since: datetime) -> Iterable[RawItem]: ...
    def to_markdown(self, item: RawItem) -> str: ...
```

`fetch()` returns items newer than `since`. `to_markdown()` converts one item to a markdown file body (frontmatter + content). The pipeline takes care of where to write it, manifest bookkeeping, and triggering the LLM.

## Walkthrough — Pocket / read-later

### Step 1 — Implement the connector

Create `muninn/sources/pocket.py`:

```python
from datetime import datetime
from typing import Iterable
import httpx
from .base import Source, RawItem

class PocketSource(Source):
    def fetch(self, since: datetime) -> Iterable[RawItem]:
        resp = httpx.post(
            "https://getpocket.com/v3/get",
            json={
                "consumer_key": self.options["consumer_key"],
                "access_token": self.secret,
                "since": int(since.timestamp()),
                "detailType": "complete",
            },
        )
        resp.raise_for_status()
        for item_id, item in resp.json()["list"].items():
            yield RawItem(
                id=item_id,
                title=item.get("resolved_title") or item.get("given_title"),
                url=item.get("resolved_url"),
                timestamp=datetime.fromtimestamp(int(item["time_added"])),
                payload=item,
            )

    def to_markdown(self, item: RawItem) -> str:
        return (
            f"---\nsource_type: pocket\nsource_url: {item.url}\n"
            f"read_date: {item.timestamp.isoformat()}\n"
            f"title: \"{item.title}\"\n---\n\n"
            f"{item.payload.get('excerpt','')}\n"
        )
```

### Step 2 — Register in `wiki.yaml`

```yaml
- name: my-pocket
  type: pocket
  enabled: true
  schedule: every_6h
  url: "https://getpocket.com/v3/get"
  secret_env: POCKET_ACCESS_TOKEN
  tool: muninn.sources.pocket:PocketSource
  options:
    consumer_key: "your-pocket-consumer-key"
```

### Step 3 — Add the secret

In `.env`:

```
POCKET_ACCESS_TOKEN=...
```

### Step 4 — Test

```bash
muninn ingest --source my-pocket --limit 3 -v
```

If the connector throws, you'll see the trace. Fix and re-run.

### Step 5 — Let the scheduler take over

No further action — the LaunchAgent picks up enabled sources on its next run.

## The "tool" field explained

The `tool:` field in YAML is a Python dotted path like `muninn.sources.pocket:PocketSource`. The pipeline imports it dynamically. This is the literal "tool to interact with the data source" — you can also point this at a third-party package if you want to keep connectors out of this repo (`my_company.connectors:JiraSource` for example).

## Future connector ideas

- YouTube watch history (Google Takeout export)
- ChatGPT / Claude conversation exports
- Goodreads notes
- Linear / Jira tickets you've touched
- Calendar events with attached meeting notes
- iMessage / Telegram exports

PRs welcome.
