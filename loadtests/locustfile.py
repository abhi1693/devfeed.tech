"""Read-only public reader journeys. Run as a separate Locust process, never inside pytest."""

import logging
import math
import random
from urllib.parse import quote

from locust import HttpUser, between, events, task
from locust.exception import StopUser
from locust.runners import WorkerRunner
from policy import gate_failures, valid_payload, validate_target

logger = logging.getLogger(__name__)
REQUIRED = {
    "/v1/feed [latest]",
    "/v1/articles/[id]",
    "/v1/feed/options",
    "/v1/topics",
    "/v1/sources",
}


@events.init_command_line_parser.add_listener
def arguments(parser):
    parser.add_argument("--allow-remote-target", action="store_true", default=False)
    parser.add_argument("--include-search", action="store_true", default=False)
    parser.add_argument("--max-failure-ratio", type=float, default=0.0)
    parser.add_argument("--max-p95-ms", type=float, default=2000.0)
    parser.add_argument("--minimum-requests", type=int, default=50)


@events.quitting.add_listener
def quality_gate(environment, **kwargs):
    if isinstance(environment.runner, WorkerRunner):
        return
    options = environment.parsed_options
    required = REQUIRED | ({"/v1/search"} if options.include_search else set())
    failures = gate_failures(
        environment.stats.total,
        list(environment.stats.entries.values()),
        minimum=options.minimum_requests,
        max_failure_ratio=options.max_failure_ratio,
        max_p95_ms=options.max_p95_ms,
        required=required,
    )
    if environment.runner and environment.runner.exceptions:
        failures.append("Unhandled Locust user exception")
    for failure in failures:
        logger.error("Load gate: %s", failure)
    if failures:
        environment.process_exit_code = 1
    elif environment.process_exit_code in (None, 0):
        environment.process_exit_code = 0


class PublicReader(HttpUser):
    """Browse, paginate, open articles and change discovery filters with think time."""

    wait_time = between(0.2, 1.0)

    def on_start(self):
        options = self.environment.parsed_options
        try:
            validate_target(self.host, options.allow_remote_target)
            if not (
                0 <= options.max_failure_ratio <= 1
                and math.isfinite(options.max_p95_ms)
                and options.max_p95_ms > 0
            ):
                raise ValueError("Failure ratio must be 0..1 and p95 must be positive")
            if options.minimum_requests < 1:
                raise ValueError("Minimum requests must be positive")
        except ValueError as exc:
            logger.error("Invalid load configuration: %s", exc)
            self.environment.process_exit_code = 2
            raise StopUser from exc
        self.articles = []
        self.cursor = None
        self.topics = []
        self.sources = []
        self.latest()
        self.discovery()
        self.article()
        if options.include_search:
            self.search()
        if not self.articles or not self.topics or not self.sources:
            logger.error(
                "Load target needs published articles with active topics and approved sources"
            )
            self.environment.events.request.fire(
                request_type="DATA",
                name="bootstrap",
                response_time=0,
                response_length=0,
                exception=ValueError("Load fixture is empty or incomplete"),
                context={},
            )
            self.environment.process_exit_code = 1
            raise StopUser

    def read(self, path, name, kind, **params):
        with self.client.get(
            path,
            name=name,
            params=params,
            timeout=10,
            allow_redirects=False,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return None
            try:
                data = response.json()
            except ValueError:
                response.failure("Expected JSON, received invalid response")
                return None
            if not valid_payload(kind, data):
                response.failure("Unexpected response schema")
                return None
            return data

    @task(6)
    def latest(self):
        data = self.read("/v1/feed", "/v1/feed [latest]", "feed", limit=30, diverse="true")
        if data is not None:
            self.articles = data["items"]
            self.cursor = data["next_cursor"]

    @task(3)
    def next_page(self):
        if not self.cursor:
            self.latest()
            return
        data = self.read(
            "/v1/feed", "/v1/feed [page]", "feed", limit=30, diverse="true", cursor=self.cursor
        )
        if data is not None:
            self.articles = data["items"] or self.articles
            self.cursor = data["next_cursor"]

    @task(5)
    def article(self):
        if self.articles:
            identifier = quote(random.choice(self.articles)["id"], safe="")
            self.read(f"/v1/articles/{identifier}", "/v1/articles/[id]", "article")

    @task(2)
    def discovery(self):
        self.read("/v1/feed/options", "/v1/feed/options", "options")
        topics = self.read("/v1/topics", "/v1/topics", "topics", limit=30, has_articles="true")
        sources = self.read("/v1/sources", "/v1/sources", "sources", limit=30, has_articles="true")
        if topics is not None:
            self.topics = topics
        if sources is not None:
            self.sources = sources

    @task(3)
    def filtered_feed(self):
        if self.topics and self.sources:
            if random.choice((True, False)):
                filters = {"topic": random.choice(self.topics)["slug"]}
                name = "/v1/feed [topic]"
            else:
                filters = {"source_id": random.choice(self.sources)["id"]}
                name = "/v1/feed [source]"
            self.read("/v1/feed", name, "feed", limit=30, **filters)

    @task(1)
    def search(self):
        if self.environment.parsed_options.include_search:
            self.read(
                "/v1/search", "/v1/search", "search", q=random.choice(("Python", "database", "API"))
            )
        else:
            self.latest()
