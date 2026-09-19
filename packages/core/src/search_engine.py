"""Typesense adapter. Query keys stay on the API; index keys stay on the indexer."""

import atexit
import json
import logging
import os
import random
import threading
import time
import uuid
from typing import cast
from urllib.parse import urlencode

import httpcore

from devfeed_core.config import get_settings
from devfeed_core.telemetry import observed_dependency

KINDS = ("articles", "topics", "sources", "tags")
PAGE_SIZE = 12
MAX_PAGE = 80
ANALYTICS_QUERY_COLLECTION = "search_queries"
ANALYTICS_NOHITS_COLLECTION = "search_nohits"
ANALYTICS_RULE_TAG = "devfeed-search"
FILTER_FIELDS = (
    {"name": "topics", "type": "string[]", "facet": True, "optional": True},
    {"name": "sources", "type": "string[]", "facet": True, "optional": True},
    {"name": "tags", "type": "string[]", "facet": True, "optional": True},
    {"name": "content_type", "type": "string", "facet": True, "optional": True},
)
CONTENT_TYPES = frozenset(("article", "news", "tutorial", "release", "comparison", "opinion"))
logger = logging.getLogger(__name__)


def _escape_filter_value(value):
    """Quote one server-constructed Typesense filter value."""
    return "`" + str(value).replace("\\", "\\\\").replace("`", "\\`") + "`"


def _typed_filter(field, values):
    """Build an exact filter from validated values, never from filter syntax."""
    escaped = []
    for value in values:
        if field in {"topics", "sources", "tags"}:
            try:
                value = uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"Invalid {field} filter") from exc
        elif field == "content_type" and value not in CONTENT_TYPES:
            raise ValueError("Invalid content_type filter")
        escaped.append(_escape_filter_value(value))
    return f"{field}:=[{','.join(escaped)}]"


_pool_lock = threading.Lock()
_pool: httpcore.ConnectionPool | None = None
_pool_pid: int | None = None


def _get_connection_pool() -> httpcore.ConnectionPool:
    """Return the process-local pool used by all Typesense requests."""
    global _pool, _pool_pid

    pid = os.getpid()
    if _pool is None or _pool_pid != pid:
        with _pool_lock:
            if _pool is None or _pool_pid != pid:
                # Do not reuse a pool created before a fork. A worker must not
                # share sockets with its parent or another worker process.
                settings = get_settings()
                _pool = httpcore.ConnectionPool(
                    max_connections=settings.search_http_max_connections,
                    retries=0,
                )
                _pool_pid = pid
    return _pool


def _close_connection_pool() -> None:
    global _pool, _pool_pid

    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
            _pool_pid = None


atexit.register(_close_connection_pool)


class SearchUnavailable(Exception):
    """Safe public error without upstream bodies, URLs or credentials."""


class SearchRequestError(SearchUnavailable):
    """A sanitized Typesense request failure with retry classification."""

    def __init__(self, message, *, reason, status=None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class SearchImportError(SearchUnavailable):
    """A bulk import response that could not be fully acknowledged."""

    def __init__(self, message, *, reason):
        super().__init__(message)
        self.reason = reason


def _runtime_metrics():
    from devfeed_core.telemetry import current

    runtime = current()
    return runtime.metrics if runtime else None


class Typesense:
    def __init__(self, *, admin=False):
        settings = get_settings()
        key = settings.search_admin_key if admin else settings.search_query_key
        if not settings.search_enabled or not settings.search_url or not key:
            raise SearchUnavailable("Search is unavailable")
        self.origin, self.key = settings.search_url, key.get_secret_value()
        self.prefix = settings.search_collection_prefix
        self.timeout = 10.0 if admin else 1.5
        self.write_timeout = settings.search_index_write_timeout_seconds
        self.write_timeout_max = settings.search_index_write_timeout_max_seconds

    def alias(self, kind):
        if kind not in KINDS:
            raise ValueError("Unknown search section")
        return f"{self.prefix}_{kind}"

    def physical_collection(self, kind, version=None):
        if kind not in KINDS:
            raise ValueError("Unknown search section")
        version = version or get_settings().search_collection_version
        if (
            not isinstance(version, str)
            or not version
            or not version.replace("_", "").replace("-", "").isalnum()
        ):
            raise ValueError("Invalid search collection version")
        return f"{self.prefix}_{kind}_{version}"

    def collection(self, kind):
        """Return the stable alias used by readers and the live indexer."""
        return self.alias(kind)

    @observed_dependency("typesense", "request")
    def request(self, method, path, *, data=None, raw=None, allowed=(), timeout=None):
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        request_timeout = self.timeout if timeout is None else timeout
        try:
            with _get_connection_pool().stream(
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
                        "read": request_timeout,
                        "write": request_timeout,
                        # Bound burst queueing for this process. The pool is
                        # shared by reads and writes, so use the smaller of
                        # the request timeout and configured wait policy.
                        "pool": min(
                            request_timeout,
                            get_settings().search_http_pool_timeout_seconds,
                        ),
                    }
                },
            ) as response:
                if response.status in allowed:
                    return None
                if response.status >= 400:
                    reason = "http_503" if response.status == 503 else "http_error"
                    raise SearchRequestError(
                        "Search is unavailable", reason=reason, status=response.status
                    )
                payload = bytearray()
                for chunk in response.iter_stream():
                    payload.extend(chunk)
                    if len(payload) > 4_000_000:
                        raise SearchUnavailable("Search response exceeded its limit")
            return bytes(payload)
        except httpcore.TimeoutException as exc:
            raise SearchRequestError("Search is unavailable", reason="timeout") from exc
        except (httpcore.NetworkError, httpcore.ProtocolError) as exc:
            raise SearchRequestError("Search is unavailable", reason="network_error") from exc
        except ValueError as exc:
            raise SearchRequestError("Search is unavailable", reason="response_error") from exc

    def _retry_delay(self, retry_count):
        settings = get_settings()
        ceiling = min(
            settings.search_index_retry_max_seconds,
            settings.search_index_retry_base_seconds * 2**retry_count,
        )
        return random.uniform(ceiling * 0.5, ceiling)

    def _with_retries(self, operation, *, collection, batch_size, timeout=None):
        settings = get_settings()
        attempts = settings.search_index_retry_attempts
        for retry_count in range(attempts + 1):
            started = time.monotonic()
            try:
                result = operation(timeout)
            except (SearchRequestError, SearchImportError) as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                retryable = isinstance(exc, SearchImportError) or exc.reason in {
                    "http_503",
                    "network_error",
                    "timeout",
                }
                logger.warning(
                    "search_index_attempt_failed",
                    extra={
                        "collection": collection,
                        "batch_size": batch_size,
                        "status": getattr(exc, "status", None),
                        "duration_ms": duration_ms,
                        "retry_count": retry_count,
                        "error_type": type(exc).__name__,
                        "failure_reason": exc.reason,
                        "retryable": retryable and retry_count < attempts,
                    },
                )
                if not retryable or retry_count >= attempts:
                    raise
                if metrics := _runtime_metrics():
                    metrics.search_documents.labels(metrics.service, collection, "retried").inc(
                        batch_size
                    )
                delay = self._retry_delay(retry_count)
                if exc.reason == "timeout":
                    timeout = min(
                        self.write_timeout_max,
                        max(timeout or self.write_timeout, (timeout or self.write_timeout) * 2),
                    )
                time.sleep(delay)
            else:
                return result, int((time.monotonic() - started) * 1000)

        raise AssertionError("unreachable")

    def _import(self, collection, raw, batch_size):
        def operation(timeout):
            payload = self.request(
                "POST",
                f"/collections/{collection}/documents/import?action=upsert",
                raw=raw,
                timeout=timeout,
            )
            failed_lines = 0
            lines = []
            for line in payload.splitlines():
                try:
                    lines.append(json.loads(line))
                except (ValueError, TypeError, AttributeError):
                    failed_lines += 1
            failed_lines = max(failed_lines, batch_size - len(lines), 0)
            if len(lines) != batch_size:
                if metrics := _runtime_metrics():
                    metrics.search_failed_import_lines.labels(metrics.service, collection).inc(
                        max(failed_lines, 1)
                    )
                raise SearchImportError(
                    "Search import returned an incomplete response", reason="malformed"
                )
            if not all(isinstance(line, dict) for line in lines):
                if metrics := _runtime_metrics():
                    metrics.search_failed_import_lines.labels(metrics.service, collection).inc(
                        batch_size
                    )
                raise SearchImportError("Search import returned malformed data", reason="malformed")
            rejected_lines = sum(line.get("success") is not True for line in lines)
            if rejected_lines:
                if metrics := _runtime_metrics():
                    metrics.search_failed_import_lines.labels(metrics.service, collection).inc(
                        rejected_lines
                    )
                    metrics.search_documents.labels(metrics.service, collection, "rejected").inc(
                        rejected_lines
                    )
                raise SearchImportError(
                    "Search import rejected one or more documents", reason="rejected_write"
                )
            return None

        _, duration_ms = self._with_retries(
            operation,
            collection=collection,
            batch_size=batch_size,
            timeout=self.write_timeout,
        )
        self.write_timeout = min(
            self.write_timeout_max,
            max(self.write_timeout, duration_ms / 1000 * 3),
        )
        if metrics := _runtime_metrics():
            metrics.search_documents.labels(metrics.service, collection, "imported").inc(batch_size)

    def search(
        self,
        query,
        kinds=KINDS,
        page=1,
        *,
        sort="relevance",
        date_from=None,
        date_to=None,
        topics=None,
        sources=None,
        tags=None,
        content_types=None,
    ):
        searches = [
            {
                "collection": self.collection(kind),
                "q": query,
                "query_by": "title,terms,description,author",
                "query_by_weights": "10,5,2,1",
                "prefix": len(query) > 1,
                # The relevance baseline retains the typo recovery needed for
                # queries such as "kuberentes networking" while avoiding the
                # broader false-match surface of two edits.
                "num_typos": 1 if len(query) > 2 else 0,
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
        article_sort = {
            "relevance": "_text_match:desc,published_at:desc",
            "newest": "published_at:desc,_text_match:desc",
            "oldest": "published_at:asc,_text_match:desc",
        }[sort]
        for kind, search in zip(kinds, searches, strict=True):
            if kind != "articles":
                continue
            search["sort_by"] = article_sort
            filters = []
            if date_from is not None:
                filters.append(f"published_at:>={int(date_from)}")
            if date_to is not None:
                filters.append(f"published_at:<{int(date_to)}")
            for field, values in (
                ("topics", topics),
                ("sources", sources),
                ("tags", tags),
                ("content_type", content_types),
            ):
                if values:
                    filters.append(_typed_filter(field, values))
            if filters:
                search["filter_by"] = " && ".join(filters)
        started = time.monotonic()
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
            if metrics := _runtime_metrics():
                for result in results:
                    search_time_ms = result.get("search_time_ms")
                    if isinstance(search_time_ms, (int, float)):
                        metrics.search_time.labels(metrics.service).observe(search_time_ms / 1000)
            return dict(zip(kinds, results, strict=True))
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchUnavailable("Search is unavailable") from exc
        finally:
            if metrics := _runtime_metrics():
                metrics.search_latency.labels(metrics.service).observe(time.monotonic() - started)

    def count(self, kind):
        collection = self.alias_target(kind)
        if collection is None:
            raise SearchUnavailable("Search alias is unavailable")

        def operation(timeout):
            payload = self.request(
                "GET",
                f"/collections/{collection}",
                timeout=timeout,
            )
            try:
                found = json.loads(payload)["num_documents"]
                if not isinstance(found, int) or found < 0:
                    raise ValueError("Invalid collection count")
                return found
            except (ValueError, TypeError, KeyError) as exc:
                raise SearchImportError("Search count is unavailable", reason="malformed") from exc

        result, _ = self._with_retries(
            operation,
            collection=collection,
            batch_size=0,
            timeout=self.timeout,
        )
        return result

    def create_collection(self, kind, *, version=None, allowed=(409,), timeout=None):
        collection = self.physical_collection(kind, version)
        return self.request(
            "POST",
            "/collections",
            data={
                "name": collection,
                "symbols_to_index": ["+", "#", "."],
                "token_separators": ["-", "_", "/"],
                "fields": [
                    {"name": "title", "type": "string"},
                    {"name": "description", "type": "string"},
                    {"name": "terms", "type": "string[]"},
                    {"name": "author", "type": "string"},
                    {"name": "published_at", "type": "int64"},
                    *FILTER_FIELDS,
                ],
                "default_sorting_field": "published_at",
            },
            allowed=allowed,
            timeout=self.write_timeout if timeout is None else timeout,
        )

    def ensure_filter_fields(self, kind):
        """Add filter fields to an existing physical collection during setup."""
        collection = self.physical_collection(kind)
        payload = self.request("GET", f"/collections/{collection}")
        try:
            existing = {field["name"] for field in json.loads(payload).get("fields", [])}
        except (ValueError, TypeError, AttributeError):
            raise SearchUnavailable("Search schema is unavailable") from None
        missing = [field for field in FILTER_FIELDS if field["name"] not in existing]
        if missing:
            self.request(
                "PATCH",
                f"/collections/{collection}",
                data={"fields": missing},
                timeout=self.write_timeout,
            )

    def alias_target(self, kind, *, timeout=None):
        payload = self.request(
            "GET", f"/aliases/{self.alias(kind)}", allowed=(404,), timeout=timeout
        )
        if payload is None:
            return None
        try:
            target = json.loads(payload)["collection_name"]
            if not isinstance(target, str) or not target:
                raise ValueError("Invalid alias response")
            return target
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchUnavailable("Search alias is unavailable") from exc

    def analytics_rules(self):
        """Return configured Typesense analytics rules without exposing credentials."""
        payload = self.request("GET", "/analytics/rules")
        try:
            value = json.loads(payload)
            rules = value.get("rules", value) if isinstance(value, dict) else value
            if not isinstance(rules, list):
                raise ValueError("Invalid analytics rules response")
            return rules
        except (ValueError, TypeError, AttributeError) as exc:
            raise SearchUnavailable("Search analytics is unavailable") from exc

    def analytics_queries(self, collection, *, limit=25):
        """Read aggregated query documents for the private admin dashboard."""
        params = urlencode(
            {
                "q": "*",
                "query_by": "q",
                "sort_by": "count:desc",
                "per_page": limit,
                "include_fields": "q,count",
            }
        )
        payload = self.request("GET", f"/collections/{collection}/documents/search?{params}")
        try:
            hits = json.loads(payload).get("hits", [])
            if not isinstance(hits, list):
                raise ValueError("Invalid analytics query response")
            result = []
            for item in hits:
                document = item.get("document", {})
                query, count = document.get("q"), document.get("count")
                if isinstance(query, str) and isinstance(count, int) and count >= 0:
                    result.append({"query": query, "count": count})
            result.sort(key=lambda item: (-cast(int, item["count"]), cast(str, item["query"])))
            return result
        except (ValueError, TypeError, AttributeError) as exc:
            raise SearchUnavailable("Search analytics is unavailable") from exc

    def _create_analytics_collection(self, collection):
        self.request(
            "POST",
            "/collections",
            data={
                "name": collection,
                "fields": [
                    {"name": "q", "type": "string"},
                    {"name": "count", "type": "int32"},
                ],
                "default_sorting_field": "count",
            },
            allowed=(409,),
            timeout=self.write_timeout,
        )

    def setup_analytics(self):
        """Create the persistent query analytics projection used by the admin UI."""
        if not get_settings().search_analytics_enabled:
            return
        self._create_analytics_collection(f"{self.prefix}_{ANALYTICS_QUERY_COLLECTION}")
        self._create_analytics_collection(f"{self.prefix}_{ANALYTICS_NOHITS_COLLECTION}")
        source = self.alias("articles")
        query_collection = f"{self.prefix}_{ANALYTICS_QUERY_COLLECTION}"
        nohits_collection = f"{self.prefix}_{ANALYTICS_NOHITS_COLLECTION}"
        self.request(
            "PUT",
            "/analytics/rules/devfeed-popular-queries",
            data={
                "name": "devfeed-popular-queries",
                "type": "popular_queries",
                "collection": source,
                "event_type": "search",
                "rule_tag": ANALYTICS_RULE_TAG,
                "params": {
                    "destination_collection": query_collection,
                    "limit": 1000,
                    "expand_query": False,
                    "capture_search_requests": True,
                },
            },
            timeout=self.write_timeout,
        )
        self.request(
            "PUT",
            "/analytics/rules/devfeed-nohits-queries",
            data={
                "name": "devfeed-nohits-queries",
                "type": "nohits_queries",
                "collection": source,
                "event_type": "search",
                "rule_tag": ANALYTICS_RULE_TAG,
                "params": {
                    "destination_collection": nohits_collection,
                    "limit": 1000,
                    "expand_query": False,
                    "capture_search_requests": True,
                },
            },
            timeout=self.write_timeout,
        )

    def setup(self):
        for kind in KINDS:
            collection = self.collection(kind)
            self._with_retries(
                lambda timeout, kind=kind: self.create_collection(kind, timeout=timeout),
                collection=self.physical_collection(kind),
                batch_size=0,
                timeout=self.write_timeout,
            )
            self._with_retries(
                lambda timeout, kind=kind: self.ensure_filter_fields(kind),
                collection=self.physical_collection(kind),
                batch_size=0,
                timeout=self.write_timeout,
            )
            current, _ = self._with_retries(
                lambda timeout, kind=kind: self.alias_target(kind, timeout=timeout),
                collection=collection,
                batch_size=0,
                timeout=self.timeout,
            )
            if current is None:
                self._with_retries(
                    lambda timeout, collection=collection, kind=kind: self.request(
                        "PUT",
                        f"/aliases/{collection}",
                        data={"collection_name": self.physical_collection(kind)},
                        timeout=timeout,
                    ),
                    collection=collection,
                    batch_size=0,
                    timeout=self.write_timeout,
                )
        self.setup_analytics()

    def sync(self, kind, documents, removed):
        # Typesense aliases are the reader contract; document writes and
        # deletes must target the current physical collection explicitly.
        collection = self.alias_target(kind)
        if collection is None:
            raise SearchUnavailable("Search alias is unavailable")
        if documents:
            raw = b"\n".join(json.dumps(doc).encode() for doc in documents)
            self._import(collection, raw, len(documents))
        if removed:
            params = urlencode(
                {"filter_by": "id:=[" + ",".join(str(value) for value in removed) + "]"}
            )
            self._with_retries(
                lambda timeout: self.request(
                    "DELETE", f"/collections/{collection}/documents?{params}", timeout=timeout
                ),
                collection=collection,
                batch_size=len(removed),
                timeout=self.write_timeout,
            )
            if metrics := _runtime_metrics():
                metrics.search_documents.labels(metrics.service, collection, "deleted").inc(
                    len(removed)
                )
        if metrics := _runtime_metrics():
            metrics.search_last_success.labels(metrics.service, collection).set(time.time())
