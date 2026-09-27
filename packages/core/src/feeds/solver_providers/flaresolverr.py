"""FlareSolverr v1 API adapter. Browser sessions/cookies never leave this adapter."""

import json
import re
import time
from urllib.parse import urlsplit

import httpcore

from devfeed_core.feeds.fetcher import FeedError, FetchResult, _fetch, is_browser_challenge
from devfeed_core.feeds.solver_types import SolveRequest, SolverUnavailable


class FlareSolverr:
    def __init__(self, url: str):
        self.url = url

    def solve(self, request: SolveRequest) -> FetchResult:
        started = time.monotonic()
        try:
            with (
                httpcore.ConnectionPool(max_connections=1, max_keepalive_connections=0) as pool,
                pool.stream(
                    "POST",
                    self.url + "/v1",
                    headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
                    content=json.dumps(
                        {
                            "cmd": "request.get",
                            "url": request.url,
                            "maxTimeout": max(1, int(request.timeout_seconds * 1000)),
                            **({"returnOnlyCookies": True} if request.image else {}),
                        }
                    ).encode(),
                    extensions={
                        "timeout": dict.fromkeys(
                            ["connect", "read", "write", "pool"], request.timeout_seconds + 5
                        )
                    },
                ) as response,
            ):
                if response.status != 200:
                    raise SolverUnavailable()
                body = bytearray()
                for chunk in response.iter_stream():
                    body.extend(chunk)
                    # JSON can escape each HTML character as six bytes.
                    if len(body) > request.max_bytes * 6 + 65536:
                        raise FeedError(
                            "Solver response exceeds maximum size",
                            reason="response_too_large",
                            limit_bytes=request.max_bytes,
                            limit_setting=request.limit_setting,
                        )
                    if time.monotonic() - started > request.timeout_seconds + 5:
                        raise SolverUnavailable(work_may_continue=True)
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("status") != "ok":
                raise SolverUnavailable()
            solution = data.get("solution")
            if not isinstance(solution, dict):
                raise SolverUnavailable()
            url, html, status = (
                solution.get("url"),
                solution.get("response"),
                solution.get("status"),
            )
            headers = solution.get("headers", {})
            if (
                not isinstance(url, str)
                or (not request.image and not isinstance(html, str))
                or not isinstance(headers, dict)
            ):
                raise SolverUnavailable()
            headers = {str(k).lower(): str(v) for k, v in headers.items()}
            if type(status) is not int or status != 200 or is_browser_challenge(headers):
                raise FeedError("Challenge remains unresolved", reason="browser_challenge")
            if request.image:
                # FlareSolverr returns browser cookies/UA, not binary image bytes.
                # Replay only to the requested origin through the DNS-pinned transport.
                remaining = request.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise SolverUnavailable()
                return _fetch(
                    request.url,
                    None,
                    None,
                    accept="image/*",
                    max_bytes=request.max_bytes,
                    timeout=min(15, remaining),
                    limit_setting=request.limit_setting,
                    deadline=time.monotonic() + remaining,
                    origin_headers=image_headers(solution, request.url),
                )
            # This API's 200 is synthetic. Shared validation also rejects soft
            # error/challenge documents before any pipeline extracts metadata.
            assert isinstance(html, str)
            return FetchResult(
                status,
                html.encode(),
                url,
                content_type=headers.get("content-type", "text/html").split(";", 1)[0]
                + "; charset=utf-8",
            )
        except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
            raise SolverUnavailable() from exc
        except (httpcore.NetworkError, httpcore.TimeoutException, httpcore.ProtocolError) as exc:
            # A disconnected client does not guarantee that the remote browser stopped.
            raise SolverUnavailable(work_may_continue=True) from exc
        except (ValueError, TypeError, UnicodeError) as exc:
            raise SolverUnavailable() from exc


def image_headers(solution: dict, url: str) -> dict[str, str]:
    """Do not export credentials, forward foreign cookies, or permit header injection."""
    agent = solution.get("userAgent")
    if (
        not isinstance(agent, str)
        or not 1 <= len(agent) <= 1024
        or any(ord(char) < 32 or ord(char) > 126 for char in agent)
    ):
        raise SolverUnavailable()
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or "/"
    cookies = solution.get("cookies", [])
    if not isinstance(cookies, list) or len(cookies) > 100:
        raise SolverUnavailable()
    values = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", "")).lower()
        if domain != host and not (
            domain.startswith(".") and (host == domain[1:] or host.endswith(domain))
        ):
            continue
        prefix = str(cookie.get("path", "/"))
        if not (
            path == prefix
            or (
                path.startswith(prefix)
                and (prefix.endswith("/") or path[len(prefix) :].startswith("/"))
            )
        ):
            continue
        if cookie.get("secure") and parts.scheme != "https":
            continue
        expiry = cookie.get("expires")
        if isinstance(expiry, (int, float)) and 0 < expiry < time.time():
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
            or len(value) > 4096
            or any(ord(char) < 33 or ord(char) > 126 or char in ';,"\\' for char in value)
        ):
            raise SolverUnavailable()
        values.append(f"{name}={value}")
    header = "; ".join(values)
    if len(header) > 8192:
        raise SolverUnavailable()
    return {"User-Agent": agent, **({"Cookie": header} if header else {})}
