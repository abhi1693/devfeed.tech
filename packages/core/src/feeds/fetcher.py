"""Bounded public transport for RSS admission, ingestion and article HTML lookup."""

import ipaddress
import logging
import socket
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlsplit

import httpcore

from devfeed_core.config import get_settings
from devfeed_core.logging import elapsed_ms
from devfeed_core.urls import validate_public_url

logger = logging.getLogger(__name__)


class FeedError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        retry_after: int = 0,
        reason: str = "feed_error",
        limit_bytes: int | None = None,
        limit_setting: str | None = None,
        resource: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after
        self.reason = reason
        self.limit_bytes = limit_bytes
        self.limit_setting = limit_setting
        self.resource = resource


class PublicNetworkBackend(httpcore.SyncBackend):
    """Resolve once, check every address, connect to a validated literal IP.

    HTTP core retains the original hostname for Host, TLS SNI and certificate checks.
    Every redirected connection goes through the same guard. No environment proxies.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise httpcore.ConnectError("Feed DNS resolution failed") from exc
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise FeedError(
                "Feed hostname resolves to a private or reserved address", reason="private_address"
            )
        last_error = None
        started = time.monotonic()
        for address in dict.fromkeys(item[4][0] for item in addresses):
            remaining = max(0.1, timeout - (time.monotonic() - started)) if timeout else None
            try:
                return super().connect_tcp(
                    str(address), port, remaining, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None


def retry_after_seconds(value: str | None) -> int:
    if not value:
        return 0
    try:
        seconds = int(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            seconds = int((date - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, min(seconds, 86400))


def fetch_feed(url: str, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
    settings = get_settings()
    return _fetch(
        url,
        etag,
        last_modified,
        accept="application/atom+xml, application/rss+xml, application/xml, text/xml",
        max_bytes=settings.feed_max_bytes,
        timeout=settings.feed_timeout_seconds,
        limit_setting="DEVFEED_FEED_MAX_BYTES",
    )


def fetch_page(url: str) -> FetchResult:
    """Bounded HTML fetch with the same DNS pinning and redirect guards as RSS."""
    settings = get_settings()
    return _fetch_html_with_solver_fallback(
        url,
        None,
        None,
        accept="text/html, application/xhtml+xml",
        max_bytes=settings.page_max_bytes,
        timeout=settings.page_timeout_seconds,
        html_only=True,
        limit_setting="DEVFEED_PAGE_MAX_BYTES",
    )


def fetch_evidence_page(url: str, timeout: float) -> FetchResult:
    """Use the same SSRF guards, with the verification run's remaining time budget."""
    settings = get_settings()
    return _fetch(
        url,
        None,
        None,
        accept="text/html, application/xhtml+xml",
        max_bytes=settings.page_max_bytes,
        timeout=min(timeout, settings.page_timeout_seconds),
        html_only=True,
        limit_setting="DEVFEED_PAGE_MAX_BYTES",
    )


def fetch_article_page(url: str) -> FetchResult:
    """Fetch complete article HTML with a separate budget for script-heavy pages."""
    settings = get_settings()
    return _fetch_html_with_solver_fallback(
        url,
        None,
        None,
        accept="text/html, application/xhtml+xml",
        max_bytes=settings.article_page_max_bytes,
        timeout=settings.page_timeout_seconds,
        html_only=True,
        limit_setting="DEVFEED_ARTICLE_PAGE_MAX_BYTES",
    )


def fetch_source_page(url: str) -> FetchResult:
    """Fetch complete source website HTML, including script-heavy homepages."""
    settings = get_settings()
    return _fetch_html_with_solver_fallback(
        url,
        None,
        None,
        accept="text/html, application/xhtml+xml",
        max_bytes=settings.source_page_max_bytes,
        timeout=settings.page_timeout_seconds,
        html_only=True,
        limit_setting="DEVFEED_SOURCE_PAGE_MAX_BYTES",
    )


def _fetch_html_with_solver_fallback(url, *args, **kwargs) -> FetchResult:
    try:
        return _fetch(url, *args, **kwargs)
    except FeedError as exc:
        if exc.reason != "browser_challenge" or not get_settings().solver_services:
            raise
        from devfeed_core.feeds.solvers import fetch_solved_page

        return fetch_solved_page(
            url, max_bytes=kwargs["max_bytes"], limit_setting=kwargs["limit_setting"]
        )


def is_browser_challenge(headers: dict[str, str]) -> bool:
    return headers.get("cf-mitigated", "").strip().lower() == "challenge" or headers.get(
        "x-amzn-waf-action", ""
    ).strip().lower() in {"challenge", "captcha"}


def _fetch(
    url, etag, last_modified, *, accept, max_bytes, timeout, html_only=False, limit_setting=None
) -> FetchResult:
    settings = get_settings()
    current = validate_public_url(url)
    origin = urlsplit(current).netloc
    headers = {
        "User-Agent": settings.feed_user_agent,
        "Accept-Encoding": "gzip, identity",
        "Accept": accept,
    }
    started = time.monotonic()
    log_started = time.perf_counter()
    resource = "page" if html_only else "feed"
    logger.debug(f"{resource}_fetch_started")
    try:
        with httpcore.ConnectionPool(
            network_backend=PublicNetworkBackend(), max_connections=1, max_keepalive_connections=0
        ) as pool:
            for redirects in range(6):
                if time.monotonic() - started > 60:
                    raise FeedError(
                        "Feed exceeded total download deadline",
                        retryable=True,
                        reason="deadline_exceeded",
                    )
                request_headers = dict(headers)
                if urlsplit(current).netloc == origin:
                    if etag:
                        request_headers["If-None-Match"] = etag
                    if last_modified:
                        request_headers["If-Modified-Since"] = last_modified
                with pool.stream(
                    "GET",
                    # Hosts are already IDNA-normalized by validation. Encode
                    # Unicode path/query characters only for transport, keeping
                    # URI delimiters and existing escapes (including signed queries).
                    # Every redirect also passes this boundary; stored URLs keep
                    # their identity instead of being rewritten during a fetch.
                    quote(current, safe=":/?@!$&'()*+,;=%[]"),
                    headers=list(request_headers.items()),
                    extensions={
                        "timeout": dict.fromkeys(["connect", "read", "write", "pool"], timeout)
                    },
                ) as response:
                    response_headers = {
                        k.decode("ascii").lower(): v.decode("latin-1") for k, v in response.headers
                    }
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response_headers.get("location")
                        if not location:
                            raise FeedError(
                                "Redirect has no Location header", reason="invalid_redirect"
                            )
                        target = validate_public_url(urljoin(current, location))
                        if current.startswith("https:") and target.startswith("http:"):
                            raise FeedError(
                                "HTTPS to HTTP redirect is not allowed", reason="https_downgrade"
                            )
                        current = target
                        logger.debug(f"{resource}_redirect", extra={"redirects": redirects + 1})
                        continue
                    # AWS WAF uses a successful-looking 202 for browser challenges,
                    # often with an empty body for RSS Accept headers. This is not
                    # acceptance of our request by the feed publisher's application.
                    if is_browser_challenge(response_headers):
                        raise FeedError(
                            "The publisher requires browser verification and is blocking "
                            "automated requests. Use a feed URL that permits feed readers, "
                            "or ask the publisher to allow access.",
                            status=response.status,
                            reason="browser_challenge",
                        )
                    if response.status not in ({200} if html_only else {200, 304}):
                        raise FeedError(
                            f"Feed returned HTTP {response.status}",
                            status=response.status,
                            retryable=response.status in {408, 425, 429} or response.status >= 500,
                            retry_after=retry_after_seconds(response_headers.get("retry-after")),
                            reason="http_error",
                        )
                    content_type = response_headers.get("content-type", "")[:250]
                    if (
                        html_only
                        and content_type
                        and content_type.split(";", 1)[0].strip().lower()
                        not in {
                            "text/html",
                            "application/xhtml+xml",
                        }
                    ):
                        raise FeedError("Page is not HTML", reason="unsupported_content_type")
                    body = bytearray()
                    if response.status == 200:
                        encoding = response_headers.get("content-encoding", "identity").lower()
                        if encoding not in {"identity", "gzip"}:
                            raise FeedError(
                                "Feed returned an unsupported content encoding",
                                reason="unsupported_encoding",
                            )
                        decoder = (
                            zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                        )
                        received = 0
                        for chunk in response.iter_stream():
                            received += len(chunk)
                            if received > max_bytes:
                                raise FeedError(
                                    "Feed exceeds maximum response size",
                                    reason="response_too_large",
                                    status=response.status,
                                    limit_bytes=max_bytes,
                                    limit_setting=limit_setting,
                                )
                            if decoder:
                                chunk = decoder.decompress(chunk, max_bytes - len(body) + 1)
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise FeedError(
                                    "Feed exceeds maximum response size",
                                    reason="response_too_large",
                                    status=response.status,
                                    limit_bytes=max_bytes,
                                    limit_setting=limit_setting,
                                )
                            if time.monotonic() - started > 60:
                                raise FeedError(
                                    "Feed exceeded total download deadline",
                                    retryable=True,
                                    reason="deadline_exceeded",
                                )
                        if decoder and (not decoder.eof or decoder.unused_data):
                            raise FeedError("Invalid or truncated gzip feed", reason="invalid_gzip")
                    logger.debug(
                        f"{resource}_fetch_completed",
                        extra={
                            "upstream_status": response.status,
                            "bytes_received": len(body),
                            "redirects": redirects,
                            "duration_ms": elapsed_ms(log_started),
                        },
                    )
                    return FetchResult(
                        response.status,
                        bytes(body),
                        current,
                        response_headers.get("etag", "")[:1000] or None,
                        response_headers.get("last-modified", "")[:1000] or None,
                        content_type or None,
                    )
            raise FeedError("Too many feed redirects", reason="too_many_redirects")
    except (httpcore.NetworkError, httpcore.TimeoutException, httpcore.ProtocolError) as exc:
        raise FeedError(
            f"Feed transport error: {type(exc).__name__}", retryable=True, reason="transport_error"
        ) from exc
    except (ValueError, zlib.error) as exc:
        raise FeedError(
            "Feed URL or response encoding rejected", reason="unsafe_url_or_encoding"
        ) from exc
