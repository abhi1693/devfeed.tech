"""Read-only public reader journeys. Run as a separate Locust process, never inside pytest."""

import argparse
import json
import logging
import math
import os
import random
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import gevent
from gevent.event import Event
from latency import RESPONSE_TIME_BUCKET_MS, bucket_response_time
from locust import HttpUser, events, task
from locust import stats as locust_stats
from locust.exception import StopUser
from locust.runners import MasterRunner, WorkerRunner
from policy import gate_failures, valid_payload, validate_target

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = "reader-load-v2"
JOURNEYS = (
    ("latest",) * 6
    + ("next_page",) * 3
    + ("article",) * 5
    + ("discovery",) * 2
    + ("filtered_feed",) * 3
    + ("search",)
)
# Configure before any user or worker records samples. Both revisions are driven
# by this candidate harness, so their histogram resolution stays identical.
locust_stats.bucket_response_time = bucket_response_time
REQUIRED = {
    "/v1/feed [latest]",
    "/v1/articles/[id]",
    "/v1/feed/options",
    "/v1/topics",
    "/v1/sources",
}
SEARCH_REQUIRED = {
    "/v1/search [exact]",
    "/v1/search [typo]",
    "/v1/search [natural]",
    "/v1/search [zero]",
}
SEARCH_QUERIES = (
    ("Python", "/v1/search [exact]"),
    ("kuberentes networking", "/v1/search [typo]"),
    ("database connection pooling", "/v1/search [natural]"),
    ("zzzz-no-results-devfeed", "/v1/search [zero]"),
)


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Duration must be finite and positive")
    return seconds


@events.init_command_line_parser.add_listener
def arguments(parser):
    parser.add_argument("--final-json", default=None)
    parser.add_argument("--allow-remote-target", action="store_true", default=False)
    parser.add_argument("--include-search", action="store_true", default=False)
    parser.add_argument("--max-failure-ratio", type=float, default=0.0)
    parser.add_argument("--max-p95-ms", type=float, default=2000.0)
    parser.add_argument("--search-max-p95-ms", type=float, default=800.0)
    parser.add_argument("--minimum-requests", type=int, default=50)
    parser.add_argument("--journey-seed", type=int, default=294)
    parser.add_argument("--measurement-seconds", type=positive_seconds, default=None)
    parser.add_argument("--warmup-seconds", type=positive_seconds, default=15)
    parser.add_argument("--phase-file", default=None)


class MeasurementWindow:
    """Exclude ramp, bootstrap and warmup, including requests already in flight."""

    def __init__(self, environment):
        self.environment = environment
        self.options = environment.parsed_options
        self.ready = Event()
        self.parked = Event()
        self.released = Event()
        self.ready_users = set()
        self.parked_users = set()
        self.phase = "ramping"
        self.greenlet = None
        self.warmup_failures = 0
        self.report = {
            "protocol_version": PROTOCOL_VERSION,
            "phase": self.phase,
            "journey_seed": self.options.journey_seed,
            "users": self.options.num_users,
            "warmup_seconds": self.options.warmup_seconds,
            "measurement_seconds": self.options.measurement_seconds,
            "ready_users": 0,
        }
        self.publish()

    def publish(self):
        destination = self.options.phase_file
        if destination:
            path = Path(destination)
            final_json = self.options.final_json
            if final_json is None:
                raise ValueError("Phase output requires an explicit final JSON report")
            report = Path(final_json)
            report_parent = report.parent.resolve()
            resolved = path.resolve()
            if path.is_symlink() or resolved.parent != report_parent:
                raise ValueError("Phase output must be a regular sibling of the final JSON report")
            if path.name == report.name:
                raise ValueError("Phase and final JSON reports must use different files")
            # A unique, exclusively created sibling avoids following a planted
            # predictable .tmp symlink, and replacement never follows path.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".reader-phase-", dir=report_parent, delete=False
            ) as temporary:
                staged = Path(temporary.name)
                try:
                    temporary.write(json.dumps(self.report, indent=2) + "\n")
                    temporary.close()
                    os.replace(staged, resolved)
                finally:
                    staged.unlink(missing_ok=True)

    def bootstrap_complete(self, user):
        self.ready_users.add(user.user_slot)
        self.report["ready_users"] = len(self.ready_users)
        if len(self.ready_users) == self.options.num_users:
            self.ready.set()

    def before_journey(self, user):
        if self.phase == "draining":
            self.parked_users.add(user.user_slot)
            if len(self.parked_users) == self.options.num_users:
                self.parked.set()
            self.released.wait()
            # A repeatable first think time avoids releasing a synchronized
            # sixteen-request burst at the measurement boundary.
            gevent.sleep(user.wait_time())
        if self.phase in {"stopping", "finished", "failed"}:
            raise StopUser

    def spawning_complete(self, user_count, **kwargs):
        if user_count == self.options.num_users and self.greenlet is None:
            self.greenlet = gevent.spawn(self.measure)

    def measure(self):
        try:
            self.run_window()
        except Exception as error:
            logger.exception("Controlled measurement failed")
            self.phase = "failed"
            self.report.update(phase=self.phase, error=str(error))
            self.publish()
            self.environment.process_exit_code = 1
            self.environment.runner.quit()

    def run_window(self):
        if not self.ready.wait(timeout=60):
            raise ValueError("Not all requested users completed bootstrap")
        warmup_started = time.monotonic()
        self.phase = "warmup"
        self.report.update(phase=self.phase, warmup_started_at=datetime.now(UTC).isoformat())
        self.publish()
        gevent.sleep(self.options.warmup_seconds)
        self.report.update(
            warmup_finished_at=datetime.now(UTC).isoformat(),
            warmup_completed_seconds=time.monotonic() - warmup_started,
        )
        self.phase = "draining"
        if not self.parked.wait(timeout=30):
            raise ValueError("Not all requested users reached the measurement barrier")
        # Warmup latency must not select a different starting point in the RNG
        # streams, cursor or article list for the two revisions.
        for user in self.environment._reader_users.values():
            user.begin_measurement()
        self.warmup_failures = self.environment.stats.total.num_failures
        self.environment.stats.reset_all()
        started = time.monotonic()
        self.phase = "measurement"
        self.report.update(
            phase=self.phase,
            started_at=datetime.now(UTC).isoformat(),
            started_monotonic=started,
        )
        self.publish()
        self.released.set()
        gevent.sleep(self.options.measurement_seconds)
        self.phase = "stopping"
        self.environment.runner.quit()

    def stop(self, **kwargs):
        if self.phase == "stopping":
            # Locust fires test_stop after in-flight measured requests drain.
            # Keep their completions in both latency statistics and diagnostics.
            finished = time.monotonic()
            self.phase = "finished"
            self.report.update(
                phase=self.phase,
                finished_at=datetime.now(UTC).isoformat(),
                finished_monotonic=finished,
                measured_seconds=finished - self.report["started_monotonic"],
            )
            self.publish()
        if self.greenlet is not None and self.greenlet is not gevent.getcurrent():
            self.greenlet.kill()


@events.init.add_listener
def configure_measurement(environment, **kwargs):
    environment._reader_users = {}

    def reset_users(**kwargs):
        environment._reader_users = {}

    environment.events.test_start.add_listener(reset_users)
    if getattr(environment.parsed_options, "measurement_seconds", None) is None:
        return
    if isinstance(environment.runner, (MasterRunner, WorkerRunner)):
        raise ValueError("Controlled measurement requires a local load generator")
    if environment.parsed_options.num_users is None:
        environment.parsed_options.num_users = 1
    window = environment._reader_measurement = MeasurementWindow(environment)
    environment.events.spawning_complete.add_listener(window.spawning_complete)
    environment.events.test_stop.add_listener(window.stop)


@events.quitting.add_listener
def quality_gate(environment, **kwargs):
    if isinstance(environment.runner, WorkerRunner):
        return
    options = environment.parsed_options
    required = REQUIRED | (SEARCH_REQUIRED if options.include_search else set())
    failures = gate_failures(
        environment.stats.total,
        list(environment.stats.entries.values()),
        minimum=options.minimum_requests,
        max_failure_ratio=options.max_failure_ratio,
        max_p95_ms=options.max_p95_ms,
        required=required,
    )
    window = getattr(environment, "_reader_measurement", None)
    if getattr(options, "measurement_seconds", None) is not None and window is None:
        failures.append("Controlled measurement was not initialized")
    if window is not None:
        if window.phase != "finished":
            failures.append("Controlled measurement did not complete its full window")
        if window.warmup_failures:
            failures.append(f"{window.warmup_failures} requests failed during ramp or warmup")
    if options.include_search:
        for entry in environment.stats.entries.values():
            if (
                entry.name in SEARCH_REQUIRED
                and entry.num_requests >= 5
                and entry.get_response_time_percentile(0.95) > options.search_max_p95_ms
            ):
                failures.append(
                    f"{entry.name}: p95 {entry.get_response_time_percentile(0.95)}ms "
                    f"exceeds search limit {options.search_max_p95_ms}ms"
                )
    if environment.runner and environment.runner.exceptions:
        failures.append("Unhandled Locust user exception")
    if options.final_json:
        entries = [environment.stats.total, *environment.stats.entries.values()]
        Path(options.final_json).write_text(
            json.dumps(
                {
                    "response_time_bucket_ms": RESPONSE_TIME_BUCKET_MS,
                    "gate_failures": failures,
                    "entries": [
                        {
                            "name": entry.name,
                            "requests": entry.num_requests,
                            "failures": entry.num_failures,
                            "p50": entry.get_response_time_percentile(0.5),
                            "p95": entry.get_response_time_percentile(0.95),
                            "p99": entry.get_response_time_percentile(0.99),
                            "rps": entry.total_rps,
                        }
                        for entry in entries
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    for failure in failures:
        logger.error("Load gate: %s", failure)
    if failures:
        environment.process_exit_code = 1
    elif environment.process_exit_code in (None, 0):
        environment.process_exit_code = 0


class PublicReader(HttpUser):
    """Browse, paginate, open articles and change discovery filters with think time."""

    def __init__(self, environment):
        super().__init__(environment)
        users = getattr(environment, "_reader_users", None)
        if users is None:
            users = environment._reader_users = {}
        self.user_slot = len(users)
        users[self.user_slot] = self
        self.reset_random("warmup")

    def reset_random(self, phase):
        seed = getattr(self.environment.parsed_options, "journey_seed", 294)
        prefix = f"devfeed-reader:{seed}:{self.user_slot}:{phase}"
        # Separate streams keep conditional resource selection from changing the
        # next journey or think time. Slots remain stable across paired runs.
        self.journey_random = random.Random(prefix + ":journeys")
        self.resource_random = random.Random(prefix + ":resources")
        self.think_random = random.Random(prefix + ":think")

    def wait_time(self):
        return self.think_random.uniform(0.2, 1.0)

    @task
    def journey(self):
        window = getattr(self.environment, "_reader_measurement", None)
        if window is not None:
            window.before_journey(self)
        getattr(self, self.journey_random.choice(JOURNEYS))()

    def begin_measurement(self):
        self.reset_random("measurement")
        self.articles, self.cursor, self.topics, self.sources = self.bootstrap_state

    def on_start(self):
        options = self.environment.parsed_options
        try:
            validate_target(self.host, options.allow_remote_target)
            if not (
                0 <= options.max_failure_ratio <= 1
                and math.isfinite(options.max_p95_ms)
                and options.max_p95_ms > 0
                and math.isfinite(options.search_max_p95_ms)
                and options.search_max_p95_ms > 0
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
        self.bootstrap_state = (self.articles, self.cursor, self.topics, self.sources)
        window = getattr(self.environment, "_reader_measurement", None)
        if window is not None:
            window.bootstrap_complete(self)

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

    def latest(self):
        data = self.read("/v1/feed", "/v1/feed [latest]", "feed", limit=30, diverse="true")
        if data is not None:
            self.articles = data["items"]
            self.cursor = data["next_cursor"]

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

    def article(self):
        if self.articles:
            identifier = quote(self.resource_random.choice(self.articles)["id"], safe="")
            self.read(f"/v1/articles/{identifier}", "/v1/articles/[id]", "article")

    def discovery(self):
        self.read("/v1/feed/options", "/v1/feed/options", "options")
        topics = self.read("/v1/topics", "/v1/topics", "topics", limit=30, has_articles="true")
        sources = self.read("/v1/sources", "/v1/sources", "sources", limit=30, has_articles="true")
        if topics is not None:
            self.topics = topics
        if sources is not None:
            self.sources = sources

    def filtered_feed(self):
        if self.topics and self.sources:
            if self.resource_random.choice((True, False)):
                filters = {"topic": self.resource_random.choice(self.topics)["slug"]}
                name = "/v1/feed [topic]"
            else:
                filters = {"source_id": self.resource_random.choice(self.sources)["id"]}
                name = "/v1/feed [source]"
            self.read("/v1/feed", name, "feed", limit=30, **filters)

    def search(self):
        if self.environment.parsed_options.include_search:
            query, name = self.resource_random.choice(SEARCH_QUERIES)
            self.read("/v1/search", name, "search", q=query)
        else:
            self.latest()
