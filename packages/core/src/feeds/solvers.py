"""Provider-neutral challenge solving with bounded, isolated worker execution.

Each configured service must enforce public-only web egress. URL checks here
cannot police a remote browser's redirects, TLS, or subresource requests.
"""

import ipaddress
import re
import socket
import time
from contextlib import suppress
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlsplit

from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.feeds.solver_providers import create_solver
from devfeed_core.feeds.solver_types import SolveRequest, SolverUnavailable
from devfeed_core.redis import create_redis
from devfeed_core.urls import validate_public_url


def _public_url(url: str) -> str:
    try:
        url = validate_public_url(url)
        parts = urlsplit(url)
        addresses = socket.getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError("Non-public solver destination")
    except ValueError as exc:
        raise FeedError("Solver URL rejected", reason="private_address") from exc
    except socket.gaierror as exc:
        raise FeedError(
            "Solver DNS lookup failed", reason="transport_error", retryable=True
        ) from exc
    return url


class _ChallengePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_title = False
        self.in_script = False
        self.challenge = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "script":
            self.in_script = True
        if tag in {"form", "div"} and dict(attrs).get("id") in {
            "challenge-form",
            "aws-waf-captcha-container",
        }:
            self.challenge = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_title:
            self.title = (self.title + data)[:500]
        if self.in_script and re.search(r"window\.awsWafCookieDomainList\s*=", data):
            self.challenge = True


def _validate_page(result: FetchResult, request: SolveRequest) -> FetchResult:
    final = _public_url(result.final_url)
    if urlsplit(request.url).scheme == "https" and urlsplit(final).scheme != "https":
        raise FeedError("Solver HTTPS downgrade rejected", reason="unsafe_redirect")
    if result.status != 200 or not result.body.strip():
        raise FeedError("Solver returned no successful page", reason="browser_challenge")
    if (result.content_type or "").split(";", 1)[0].strip().lower() not in {
        "text/html",
        "application/xhtml+xml",
    }:
        raise FeedError("Solver page is not HTML", reason="unsupported_content_type")
    if len(result.body) > request.max_bytes:
        raise FeedError(
            "Solver page exceeds maximum response size",
            reason="response_too_large",
            limit_bytes=request.max_bytes,
            limit_setting=request.limit_setting,
        )
    html = result.body.decode("utf-8", errors="replace")
    parser = _ChallengePage()
    parser.feed(html)
    parser.close()
    text = parser.title.strip().lower()
    if (
        text.startswith(
            (
                "just a moment",
                "access denied",
                "verify you are human",
                "checking your browser",
                "robot or human",
                "403 forbidden",
                "404 not found",
                "attention required!",
                "making sure you're not a bot",
                "ddos-guard",
                "please wait",
            )
        )
        or parser.challenge
    ):
        raise FeedError("Challenge remains unresolved", reason="browser_challenge")
    return replace(result, final_url=final)


def fetch_solved_page(url: str, *, max_bytes: int, limit_setting: str) -> FetchResult:
    settings = get_settings()
    if not settings.solver_services:
        raise FeedError("Solver services disabled", reason="browser_challenge")
    url = _public_url(url)
    budget = settings.solver_timeout_seconds
    deadline = time.monotonic() + budget
    try:
        with create_redis(settings, retry=Retry(NoBackoff(), retries=0)) as client:
            lock = client.lock("devfeed:solver:request", timeout=budget + 20, blocking=False)
            if not lock.acquire(blocking=False):
                raise FeedError("Solver busy", reason="solver_busy", retryable=True, retry_after=60)
            work_may_continue = False
            last_error: FeedError = SolverUnavailable()
            try:
                for service in settings.solver_services:
                    remaining = deadline - time.monotonic()
                    if remaining < 5:
                        break
                    request = SolveRequest(
                        url, min(service.timeout_seconds, remaining), max_bytes, limit_setting
                    )
                    try:
                        result = create_solver(service).solve(request)
                        return _validate_page(result, request)
                    except SolverUnavailable as exc:
                        last_error = exc
                        work_may_continue = exc.work_may_continue
                        if work_may_continue:
                            break
                    except FeedError as exc:
                        if exc.reason != "browser_challenge":
                            raise
                        last_error = exc
                raise last_error
            finally:
                # Uncertain remote completion keeps the lease until expiry.
                if not work_may_continue:
                    with suppress(RedisError):
                        lock.release()
    except RedisError as exc:
        raise SolverUnavailable() from exc
