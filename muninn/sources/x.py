"""X (formerly Twitter) API v2 connector for Muninn.

Auth modes (auto-detected from .env):
  - **OAuth 1.0a** (all endpoints): set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
  - **Bearer token** (timeline + replies only): set X_BEARER_TOKEN

Supported ``interaction_types`` (set in wiki.yaml options):
  - ``timeline``  — user's own tweets
  - ``liked``     — tweets the user has liked  (OAuth 1.0a only)
  - ``replies``   — replies written by the user
  - ``bookmarks`` — saved posts (OAuth 1.0a only)

API docs: https://developer.x.com/en/docs/x-api
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime
from typing import Any, Iterable

import httpx
from dateutil import parser as dtparse

from .base import RawItem, Source

log = logging.getLogger(__name__)

API_BASE = "https://api.twitter.com/2"
_USER_ID_RE = re.compile(r"/users/(\d+)/tweets")


class XSource(Source):
    """X v2 connector. URL is the endpoint; e.g.:
       https://api.twitter.com/2/users/<id>/tweets    (timeline)
       https://api.twitter.com/2/lists/<id>/tweets    (list)

    Options::

        interaction_types:   # list; defaults to ["timeline"]
          - timeline
          - liked
          - replies
        user_id: "12345"     # required for 'liked'
        handle: "someuser"   # required for 'replies'
    """

    @property
    def source_type(self) -> str:
        return "x"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch(self, since: datetime | None) -> Iterable[RawItem]:
        interaction_types: list[str] = self.options.get(
            "interaction_types", ["timeline"]
        )

        auth, auth_mode = self._build_auth()
        log.info("%s: using %s auth", self.name, auth_mode)

        oauth1_only = {"liked"}           # needs OAuth 1.0a (bearer won't work)
        oauth2_only = {"bookmarks"}       # needs OAuth 2.0 PKCE (neither bearer nor 1.0a works)
        with httpx.Client(base_url=API_BASE, auth=auth, timeout=30.0) as client:
            for itype in interaction_types:
                if itype in oauth2_only:
                    log.warning(
                        "%s: '%s' requires OAuth 2.0 PKCE — not supported yet. Skipping.",
                        self.name, itype,
                    )
                    continue
                if itype in oauth1_only and auth_mode == "bearer":
                    log.warning(
                        "%s: '%s' requires OAuth 1.0a — skipping (only bearer token configured). "
                        "Set X_API_KEY etc in .env.",
                        self.name, itype,
                    )
                    continue
                yield from self._fetch_for_type(client, itype, since)

    def _build_auth(self) -> tuple[Any, str]:
        """Auto-detect OAuth 1.0a vs bearer from env vars."""
        api_key = os.environ.get("X_API_KEY")
        api_secret = os.environ.get("X_API_SECRET")
        access_token = os.environ.get("X_ACCESS_TOKEN")
        access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")

        if all([api_key, api_secret, access_token, access_token_secret]):
            return _OAuth1Auth(api_key, api_secret, access_token, access_token_secret), "oauth1"

        if self.secret:
            class _BearerAuth(httpx.Auth):
                def __init__(self, token: str):
                    self.token = token
                def auth_flow(self, request):
                    request.headers["Authorization"] = f"Bearer {self.token}"
                    yield request
            return _BearerAuth(self.secret), "bearer"

        raise RuntimeError(
            f"{self.name}: no auth configured. Set either X_BEARER_TOKEN or "
            "X_API_KEY + X_API_SECRET + X_ACCESS_TOKEN + X_ACCESS_TOKEN_SECRET in .env"
        )

    # ------------------------------------------------------------------
    # Per-interaction-type dispatch
    # ------------------------------------------------------------------

    def _fetch_for_type(
        self,
        client: httpx.Client,
        interaction_type: str,
        since: datetime | None,
    ) -> Iterable[RawItem]:
        """Build endpoint + params for *interaction_type*, paginate, and yield
        ``RawItem``s with the interaction type recorded in frontmatter."""

        max_results = int(self.options.get("max_results", 50))
        include_retweets = bool(self.options.get("include_retweets", False))
        include_replies = bool(self.options.get("include_replies", True))

        tweet_fields = (
            "id,text,author_id,created_at,in_reply_to_user_id,"
            "public_metrics,referenced_tweets,entities,conversation_id"
        )
        expansions = "author_id,referenced_tweets.id,referenced_tweets.id.author_id"
        user_fields = "id,username,name"

        params: dict[str, Any] = {
            "max_results": min(max_results, 100),
            "tweet.fields": tweet_fields,
            "expansions": expansions,
            "user.fields": user_fields,
        }

        # ---- timeline ------------------------------------------------
        if interaction_type == "timeline":
            user_id = self.options.get("user_id") or self._user_id_from_url()
            endpoint = f"/users/{user_id}/tweets"

            if since:
                params["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

            exclude: list[str] = []
            if not include_retweets:
                exclude.append("retweets")
            if not include_replies:
                exclude.append("replies")
            if exclude:
                params["exclude"] = ",".join(exclude)

        # ---- liked (requires OAuth 1.0a user context) -------------------
        elif interaction_type == "liked":
            user_id = self.options.get("user_id")
            if not user_id:
                raise RuntimeError(
                    f"{self.name}: 'user_id' option is required for "
                    f"interaction_type 'liked'"
                )
            endpoint = f"/users/{user_id}/liked_tweets"

        # ---- replies -------------------------------------------------
        elif interaction_type == "replies":
            handle = self.options.get("handle")
            if not handle:
                raise RuntimeError(
                    f"{self.name}: 'handle' option is required for "
                    f"interaction_type 'replies'"
                )
            endpoint = "/tweets/search/recent"
            params["query"] = f"from:{handle} is:reply"

            if since:
                from datetime import timedelta
                earliest = datetime.now(since.tzinfo) - timedelta(days=6)
                effective = max(since, earliest)
                params["start_time"] = effective.strftime("%Y-%m-%dT%H:%M:%SZ")

        # ---- bookmarks (requires OAuth 1.0a user context) ---------------
        elif interaction_type == "bookmarks":
            user_id = self.options.get("user_id")
            if not user_id:
                raise RuntimeError(
                    f"{self.name}: 'user_id' option is required for "
                    f"interaction_type 'bookmarks'"
                )
            endpoint = f"/users/{user_id}/bookmarks"
            # bookmarks endpoint doesn't support start_time filtering

        else:
            raise RuntimeError(
                f"{self.name}: unknown interaction_type '{interaction_type}'"
            )

        for item in self._paginate(client, endpoint, params):
            item.extra_frontmatter["interaction_type"] = interaction_type
            yield item

    def _user_id_from_url(self) -> str:
        """Extract the numeric user ID from the configured URL.

        Expects a URL like ``https://api.twitter.com/2/users/12345/tweets``.
        Falls back to returning the URL path suffix so existing list/search
        URLs keep working as before.
        """
        path = self.url
        if path.startswith(API_BASE):
            path = path[len(API_BASE):]
        m = _USER_ID_RE.search(path)
        if m:
            return m.group(1)
        # Legacy fallback: treat the whole resolved path as the endpoint.
        raise RuntimeError(
            f"{self.name}: cannot extract user_id from URL '{self.url}'. "
            f"Set 'user_id' in options explicitly."
        )

    def _paginate(
        self, client: httpx.Client, endpoint: str, params: dict[str, Any]
    ) -> Iterable[RawItem]:
        next_token: str | None = None
        users_seen: dict[str, dict[str, Any]] = {}

        while True:
            q = dict(params)
            if next_token:
                q["pagination_token"] = next_token
            r = client.get(endpoint, params=q)
            if r.status_code != 200:
                # Surface a useful message rather than retrying forever.
                raise RuntimeError(f"X API {r.status_code}: {r.text[:300]}")
            data = r.json()
            for u in (data.get("includes", {}) or {}).get("users", []):
                users_seen[u["id"]] = u
            for post in data.get("data", []) or []:
                yield self._post_to_item(post, users_seen)
            next_token = (data.get("meta") or {}).get("next_token")
            if not next_token:
                break

    def _post_to_item(
        self, post: dict[str, Any], users: dict[str, dict[str, Any]]
    ) -> RawItem:
        author_id = post.get("author_id", "")
        author = users.get(author_id, {})
        handle = author.get("username", author_id)
        name = author.get("name", handle)
        when = dtparse.parse(post["created_at"])
        urls = [u["expanded_url"] for u in (post.get("entities", {}) or {}).get("urls", []) or []]
        hashtags = [h["tag"] for h in (post.get("entities", {}) or {}).get("hashtags", []) or []]
        mentions = [m["username"] for m in (post.get("entities", {}) or {}).get("mentions", []) or []]
        refs = post.get("referenced_tweets") or []
        metrics = post.get("public_metrics") or {}

        body = post["text"].strip()
        body += f"\n\n---\n*From [{handle}]({post_permalink(handle, post['id'])}) at {when.isoformat()}*"

        return RawItem(
            id=post["id"],
            title=f"@{handle}: {post['text'][:80]}",
            url=post_permalink(handle, post["id"]),
            timestamp=when,
            body=body,
            extra_frontmatter={
                "post_id": post["id"],
                "author": handle,
                "author_id": author_id,
                "author_name": name,
                "conversation_id": post.get("conversation_id"),
                "in_reply_to": post.get("in_reply_to_user_id"),
                "referenced_posts": [
                    {"type": r["type"], "id": r["id"]} for r in refs
                ],
                "urls": urls,
                "hashtags": hashtags,
                "mentions": mentions,
                "metrics": metrics,
            },
        )


def post_permalink(handle: str, post_id: str) -> str:
    return f"https://x.com/{handle}/status/{post_id}"


# ---------------------------------------------------------------------------
# OAuth 1.0a — inline, no external deps
# ---------------------------------------------------------------------------

def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


class _OAuth1Auth(httpx.Auth):
    """httpx-compatible OAuth 1.0a HMAC-SHA1 signer."""

    def __init__(self, consumer_key: str, consumer_secret: str,
                 access_token: str, access_token_secret: str) -> None:
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret

    def auth_flow(self, request: httpx.Request):
        oauth_params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        all_params: dict[str, str] = {}
        if request.url.params:
            all_params.update(dict(request.url.params))
        all_params.update(oauth_params)

        sorted_params = "&".join(
            f"{_pct(k)}={_pct(v)}" for k, v in sorted(all_params.items())
        )
        base_url = str(request.url).split("?")[0]
        base_string = f"{request.method.upper()}&{_pct(base_url)}&{_pct(sorted_params)}"
        signing_key = f"{_pct(self.consumer_secret)}&{_pct(self.access_token_secret)}"

        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()
        oauth_params["oauth_signature"] = signature

        auth_header = "OAuth " + ", ".join(
            f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth_params.items())
        )
        request.headers["Authorization"] = auth_header
        yield request
