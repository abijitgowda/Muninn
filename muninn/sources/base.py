"""Source connector base class + RawItem dataclass.

Part of the Muninn local wiki system. A connector is "the tool that interacts
with the data source". Add a new connector by subclassing `Source` and
registering it in `wiki.yaml` via a dotted `tool:` path
(`my_module.my_source:MyClass`).
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import urllib.parse
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass
class RawItem:
    """One discrete unit pulled from a source (one URL, one tweet, one file)."""

    id: str
    title: str
    url: str
    timestamp: datetime
    body: str
    extra_frontmatter: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Subclass and implement fetch + to_markdown."""

    def __init__(
        self,
        name: str,
        url: str,
        secret: str | None,
        options: dict[str, Any],
    ) -> None:
        self.name = name
        self.url = url
        self.secret = secret
        self.options = options

    @abstractmethod
    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        """Yield items newer than `since`. `since=None` means initial run."""

    def to_markdown(self, item: RawItem) -> str:
        """Default: write frontmatter + body. Override for connector-specific shape."""
        fm: dict[str, Any] = {
            "source_type": self.source_type,
            "source_url": item.url,
            "title": item.title,
            "read_date": item.timestamp.isoformat(),
            "ingested_at": datetime.now().isoformat(timespec="seconds"),
            "source_name": self.name,
        }
        fm.update(item.extra_frontmatter)
        return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + item.body.rstrip() + "\n"

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Type label used in frontmatter."""

    # ---- helpers for subclasses ----

    @staticmethod
    def validate_fetch_url(url: str) -> None:
        """Reject URLs that target private/internal networks (SSRF protection)."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("No hostname in URL")
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(hostname))
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValueError(f"URL resolves to private/internal address: {ip}")
        except socket.gaierror:
            pass

    @staticmethod
    def hash_id(*parts: str) -> str:
        h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
        return h

    @staticmethod
    def slugify(text: str, max_len: int = 60) -> str:
        s = SLUG_RE.sub("-", text.strip().lower())
        s = re.sub(r"-+", "-", s).strip("-")
        return s[:max_len] or "item"
