"""FlareSolverr v1 API adapter. Browser sessions/cookies never leave this adapter."""

import json
import time

import httpcore

from devfeed_core.feeds.fetcher import FeedError, FetchResult, is_browser_challenge
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
                or not isinstance(html, str)
                or not isinstance(headers, dict)
            ):
                raise SolverUnavailable()
            headers = {str(k).lower(): str(v) for k, v in headers.items()}
            if type(status) is not int or status != 200 or is_browser_challenge(headers):
                raise FeedError("Challenge remains unresolved", reason="browser_challenge")
            # This API's 200 is synthetic. Shared validation also rejects soft
            # error/challenge documents before any pipeline extracts metadata.
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
