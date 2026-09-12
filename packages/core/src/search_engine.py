"""Typesense adapter. Query keys stay on the API; index keys stay on the indexer."""

import json
from urllib.parse import urlencode

import httpcore

from devfeed_core.config import get_settings

KINDS = ("articles", "topics", "sources", "tags")
PAGE_SIZE = 12
MAX_PAGE = 80


class SearchUnavailable(Exception):
    """Safe public error without upstream bodies, URLs or credentials."""


class Typesense:
    def __init__(self, *, admin=False):
        settings = get_settings()
        key = settings.search_admin_key if admin else settings.search_query_key
        if not settings.search_enabled or not settings.search_url or not key:
            raise SearchUnavailable("Search is unavailable")
        self.origin, self.key = settings.search_url, key.get_secret_value()
        self.prefix = settings.search_collection_prefix
        self.timeout = 10.0 if admin else 1.5

    def collection(self, kind):
        if kind not in KINDS:
            raise ValueError("Unknown search section")
        return f"{self.prefix}_{kind}_v1"

    def request(self, method, path, *, data=None, raw=None, allowed=()):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        try:
            with (
                httpcore.ConnectionPool(max_connections=1, retries=0) as pool,
                pool.stream(
                    method,
                    self.origin + path,
                    headers={
                        "X-TYPESENSE-API-KEY": self.key,
                        "Content-Type": "text/plain" if raw is not None else "application/json",
                    },
                    content=body,
                    extensions={
                        "timeout": {
                            "connect": 0.5,
                            "read": self.timeout,
                            "write": self.timeout,
                            "pool": 0.1,
                        }
                    },
                ) as response,
            ):
                if response.status in allowed:
                    return None
                if response.status >= 400:
                    raise SearchUnavailable("Search is unavailable")
                payload = bytearray()
                for chunk in response.iter_stream():
                    payload.extend(chunk)
                    if len(payload) > 4_000_000:
                        raise SearchUnavailable("Search response exceeded its limit")
            return bytes(payload)
        except (
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            ValueError,
        ) as exc:
            raise SearchUnavailable("Search is unavailable") from exc

    def search(self, query, kinds=KINDS, page=1):
        searches = [
            {
                "collection": self.collection(kind),
                "q": query,
                "query_by": "title,terms,description,author",
                "query_by_weights": "10,5,2,1",
                "prefix": len(query) > 1,
                "num_typos": 2 if len(query) > 2 else 0,
                "drop_tokens_threshold": 0,
                "prioritize_exact_match": True,
                "sort_by": "_text_match:desc,published_at:desc",
                "per_page": PAGE_SIZE,
                "page": page,
                "limit_hits": PAGE_SIZE * MAX_PAGE,
                "include_fields": "id",
                "highlight_fields": "",
                "search_cutoff_ms": 800,
            }
            for kind in kinds
        ]
        try:
            data = json.loads(self.request("POST", "/multi_search", data={"searches": searches}))
            results = data["results"]
            if len(results) != len(kinds):
                raise ValueError("Incomplete search response")
            for result in results:
                if "error" in result or result.get("search_cutoff"):
                    raise SearchUnavailable("Search is temporarily unavailable")
                if not isinstance(result.get("hits"), list) or len(result["hits"]) > PAGE_SIZE:
                    raise ValueError("Invalid search response")
            return dict(zip(kinds, results, strict=True))
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchUnavailable("Search is unavailable") from exc

    def setup(self):
        for kind in KINDS:
            self.request(
                "POST",
                "/collections",
                data={
                    "name": self.collection(kind),
                    "symbols_to_index": ["+", "#", "."],
                    "token_separators": ["-", "_", "/"],
                    "fields": [
                        {"name": "title", "type": "string"},
                        {"name": "description", "type": "string"},
                        {"name": "terms", "type": "string[]"},
                        {"name": "author", "type": "string"},
                        {"name": "published_at", "type": "int64"},
                    ],
                    "default_sorting_field": "published_at",
                },
                allowed=(409,),
            )

    def sync(self, kind, documents, removed):
        if documents:
            raw = b"\n".join(json.dumps(doc).encode() for doc in documents)
            payload = self.request(
                "POST",
                f"/collections/{self.collection(kind)}/documents/import?action=upsert",
                raw=raw,
            )
            try:
                lines = [json.loads(line) for line in payload.splitlines()]
                if len(lines) != len(documents) or not all(
                    line.get("success") is True for line in lines
                ):
                    raise SearchUnavailable("Search import failed; changes retained for retry")
            except (ValueError, TypeError, AttributeError) as exc:
                raise SearchUnavailable("Search import failed") from exc
        if removed:
            params = urlencode(
                {"filter_by": "id:=[" + ",".join(str(value) for value in removed) + "]"}
            )
            self.request("DELETE", f"/collections/{self.collection(kind)}/documents?{params}")
