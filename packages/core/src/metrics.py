"""Process-local, bounded Prometheus metrics on a dedicated HTTP listener.

No database or network work occurs in a scrape. Forked RQ children never bind a
listener or write to their parent's registry; parent execution metrics and the
durable-state exporter deliberately measure different things.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)

from devfeed_core.version import __version__

DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)


class Metrics:
    def __init__(self, service: str, environment: str):
        self.service = service
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.build = Gauge(
            "devfeed_build_info",
            "Application artifact identity",
            ["service", "version", "environment"],
            registry=self.registry,
        )
        self.build.labels(service, __version__, environment).set(1)
        self.requests = Counter(
            "devfeed_http_requests_total",
            "Completed application HTTP requests",
            ["service", "method", "route", "status"],
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "devfeed_http_request_duration_seconds",
            "Full application response duration",
            ["service", "method", "route"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.inflight = Gauge(
            "devfeed_http_requests_in_progress",
            "Application requests in progress",
            ["service"],
            registry=self.registry,
        )
        self.dependency_duration = Histogram(
            "devfeed_dependency_request_duration_seconds",
            "Dependency call duration",
            ["service", "dependency", "operation", "result"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 240),
            registry=self.registry,
        )
        self.db_duration = Histogram(
            "devfeed_database_query_duration_seconds",
            "SQL execution duration; no SQL labels",
            ["service", "operation"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.db_errors = Counter(
            "devfeed_database_errors_total",
            "Database operation failures",
            ["service", "operation"],
            registry=self.registry,
        )
        self.pool_connections = Gauge(
            "devfeed_database_connections_checked_out",
            "Connections checked out by this process",
            ["service"],
            registry=self.registry,
        )
        self.pool_acquisition = Histogram(
            "devfeed_database_connection_acquisition_seconds",
            "Connection acquisition including pool wait, connection creation and pre-ping",
            ["service", "result"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.pool_hold = Histogram(
            "devfeed_database_connection_hold_seconds",
            "Time between connection checkout and return",
            ["service"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.executions = Counter(
            "devfeed_worker_executions_total",
            "RQ executions returned to the parent; not business successes",
            ["service", "queue"],
            registry=self.registry,
        )
        self.execution_duration = Histogram(
            "devfeed_worker_execution_duration_seconds",
            "RQ execution duration including child cleanup",
            ["service", "queue"],
            buckets=(1, 5, 15, 30, 60, 120, 240, 300),
            registry=self.registry,
        )
        self.worker_busy = Gauge(
            "devfeed_worker_busy",
            "Whether the RQ parent is executing a delivery",
            ["service"],
            registry=self.registry,
        )
        self.cycles = Counter(
            "devfeed_background_cycles_total",
            "Scheduler or indexer cycles",
            ["service", "result"],
            registry=self.registry,
        )
        self.last_success = Gauge(
            "devfeed_background_last_success_timestamp_seconds",
            "Last successful scheduler/indexer cycle",
            ["service"],
            registry=self.registry,
        )
        self.cycle_duration = Histogram(
            "devfeed_background_cycle_duration_seconds",
            "Scheduler/indexer cycle duration",
            ["service"],
            buckets=(0.1, 0.5, 1, 5, 15, 30, 60, 120),
            registry=self.registry,
        )
        self.component_up = Gauge(
            "devfeed_telemetry_component_up",
            "Instrumentation initialization state",
            ["service", "component"],
            registry=self.registry,
        )
        self.worker_busy.labels(service).set(0)
        self.inflight.labels(service).set(0)
        self.pool_connections.labels(service).set(0)
        self.last_success.labels(service).set(0)
        for result in ("success", "error"):
            self.cycles.labels(service, result).inc(0)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def listen(self, host: str, port: int) -> None:
        registry = self.registry

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                payload = generate_latest(registry)
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)
