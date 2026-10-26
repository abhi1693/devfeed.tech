"""Inside-image assertions for disposable Fleet command smoke fixtures.

Metadata setup is deliberately separate from the backend's real Alembic upgrade.
Every URL is supplied by the smoke driver on its private disposable network.
"""

import errno
import json
import os
import ssl
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from devfeed_core.db import get_engine
from devfeed_core.models import Article, Base, ReadModelEvent, UserAccount
from devfeed_core.version import SCHEMA_REVISION
from sqlalchemy import select, text
from sqlalchemy.orm import Session

ARTICLE = uuid.UUID("00000000-0000-4000-8000-000000000001")
USER = uuid.UUID("00000000-0000-4000-8000-000000000002")
TITLE = "Retain this article through the native migration"


def seed():
    with Session(get_engine()) as session, session.begin():
        session.add(
            Article(
                id=ARTICLE,
                canonical_url="https://example.invalid/native-fleet-smoke",
                url_hash="f" * 64,
                title=TITLE,
                slug="native-fleet-smoke",
                summary="Retained fixture content",
                content_type="article",
                language="en",
            )
        )
        session.add(
            UserAccount(
                id=USER,
                issuer="https://identity.invalid",
                subject="native-fleet-smoke",
                organization_id="ci",
                name="Native fixture",
                username="native-fixture",
                profile={"bio": "Retained fixture profile"},
            )
        )


def metadata_setup():
    Base.metadata.create_all(get_engine())
    with get_engine().begin() as connection:
        # This fixture tests commands independently of Alembic. The analytical
        # exporter state is migration-owned rather than part of ORM metadata.
        statements = (
            """CREATE TABLE analytics_pipeline_state (
                singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
                enabled boolean NOT NULL DEFAULT false, capture_started_at timestamptz,
                last_success_at timestamptz, lease_token uuid, lease_until timestamptz,
                lease_generation bigint NOT NULL DEFAULT 0,
                native_import_complete boolean NOT NULL DEFAULT false,
                native_capture_started_at timestamptz, reader_coverage_start date,
                native_bootstrap_barrier boolean NOT NULL DEFAULT false,
                native_bootstrap_complete boolean NOT NULL DEFAULT false)""",
            "INSERT INTO analytics_pipeline_state(singleton) VALUES(true)",
            "CREATE SEQUENCE analytics_export_generation AS bigint",
            """CREATE TABLE analytics_bootstrap_cursors (
                source_table text PRIMARY KEY, cursor jsonb,
                complete boolean NOT NULL DEFAULT false)""",
            """CREATE TABLE analytics_capture_events (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                source_table text NOT NULL, operation char(1) NOT NULL,
                record jsonb NOT NULL, before_record jsonb,
                format_version smallint NOT NULL DEFAULT 2 CHECK(format_version IN (1,2)),
                created_at timestamptz NOT NULL DEFAULT clock_timestamp())""",
            "CREATE INDEX ix_analytics_capture_created ON analytics_capture_events(created_at,id)",
            """CREATE TABLE analytics_fact_refresh (
                domain text NOT NULL, bucket timestamptz NOT NULL, generation bigint NOT NULL,
                requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                initial_pending boolean NOT NULL DEFAULT false, PRIMARY KEY(domain,bucket))""",
            "CREATE INDEX ix_analytics_fact_refresh_requested "
            "ON analytics_fact_refresh(requested_at,domain,bucket)",
            "CREATE INDEX ix_analytics_fact_refresh_due "
            "ON analytics_fact_refresh(available_at,requested_at,domain,bucket)",
            "CREATE INDEX ix_analytics_fact_refresh_domain_due "
            "ON analytics_fact_refresh(domain,available_at,requested_at,bucket)",
            """CREATE TABLE analytics_rollup_compaction (
                table_name text NOT NULL, month integer NOT NULL,
                publishes integer NOT NULL DEFAULT 0,
                PRIMARY KEY(table_name,month))""",
            """CREATE TABLE analytics_upgrade_checkpoint (
                name text PRIMARY KEY, cursor jsonb, complete boolean NOT NULL DEFAULT false)""",
            "CREATE SEQUENCE analytics_gauge_revision AS bigint",
            """CREATE TABLE analytics_gauge_refreshes (
                domain text NOT NULL CHECK(domain IN ('queues','feed','sources','topics')),
                shard smallint NOT NULL CHECK(shard BETWEEN 0 AND 255),
                revision bigint NOT NULL DEFAULT nextval('analytics_gauge_revision'),
                available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                bootstrap_required boolean NOT NULL DEFAULT false,
                bootstrap_generation bigint NOT NULL DEFAULT 0, PRIMARY KEY(domain,shard))""",
            "CREATE INDEX ix_analytics_gauge_due "
            "ON analytics_gauge_refreshes(available_at,domain,shard)",
            """CREATE TABLE analytics_gauge_bootstrap (
                singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
                started boolean NOT NULL DEFAULT false)""",
            "INSERT INTO analytics_gauge_bootstrap(singleton) VALUES(true)",
            "INSERT INTO analytics_gauge_refreshes(domain,shard) "
            "VALUES('queues',0),('feed',0),('sources',0),('topics',0)",
        )
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": SCHEMA_REVISION}
        )
    seed()
    with Session(get_engine()) as session, session.begin():
        session.add(ReadModelEvent(kind="policy"))
    print("Metadata-only disposable runtime schema; this does not prove Alembic migration")


def retained():
    with Session(get_engine()) as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
        assert session.get(Article, ARTICLE).title == TITLE
        assert session.get(UserAccount, USER).profile == {"bio": "Retained fixture profile"}


def projection():
    from devfeed_admin_api.overview_projection import (
        INDEPENDENT_GROUPS,
        REPORT_GROUPS,
        report_key,
        report_policy_version,
    )
    from devfeed_admin_api.overview_windows import current_window, recent_window
    from devfeed_admin_api.pipeline_reads import serving_projection
    from devfeed_core.models import PipelineProjection, ReportSnapshot
    from devfeed_core.read_models import pipeline_policy_version

    with Session(get_engine()) as session:
        generation, snapshot = serving_projection(session)
        assert generation == pipeline_policy_version()
        assert snapshot.status != "rebuilding"
        assert snapshot.lag_seconds is not None and snapshot.lag_seconds <= 60
        article = session.scalar(
            select(PipelineProjection).where(
                PipelineProjection.policy_version == generation,
                PipelineProjection.kind == "article",
                PipelineProjection.entity_id == ARTICLE,
            )
        )
        assert article is not None and article.payload["title"] == TITLE
        now = datetime.now(UTC)
        required = {
            report_key(
                group, current_window() if group in INDEPENDENT_GROUPS else recent_window(days, now)
            )
            for group in REPORT_GROUPS
            for days in ((1,) if group in INDEPENDENT_GROUPS else (7, 30))
        }
        assert len(REPORT_GROUPS) == 6 and len(required) == 11
        for key in required:
            report = session.get(ReportSnapshot, key)
            assert report is not None and report.policy_version == report_policy_version()
    retained()
    print("Native backfill retained records and published all 11 current section snapshots")


def heartbeat(kind):
    from devfeed_admin_api.projection_worker import heartbeat_marker

    marker = heartbeat_marker(kind)
    assert marker.is_file() and marker.stat().st_uid == 10001
    assert marker.parent.stat().st_mode & 0o777 == 0o700
    assert marker.stat().st_mode & 0o777 == 0o600
    assert 0 <= time.time() - marker.stat().st_mtime < 60
    os.utime(marker, (1, 1))


def permissions():
    assert os.geteuid() in {1000, 10001}
    assert os.getegid() == os.geteuid()
    assert Path("/tmp").is_dir()
    target = Path("/tmp/fleet-native-permission")
    target.write_text("writable tmp")
    target.unlink()
    try:
        Path("/app/fleet-must-be-readonly").write_text("unexpected")
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        raise AssertionError("Fleet root filesystem must be read-only")


def ca():
    assert os.geteuid() == 10001
    system = Path("/etc/ssl/certs/ca-certificates.crt").read_bytes()
    fixture = Path("/run/codex-ca/ca.crt").read_bytes()
    target = Path("/run/ca/ca-bundle.crt")
    directory = target.parent.stat()
    assert directory.st_uid == 0 and directory.st_gid == 10001
    assert directory.st_mode & 0o777 == 0o770
    assert target.read_bytes() == system + b"\n" + fixture
    assert target.stat().st_uid == 10001
    assert target.stat().st_gid == 10001
    assert ssl.create_default_context(cafile=str(target)).get_ca_certs()
    assert ssl.create_default_context().get_ca_certs()
    try:
        target.write_bytes(b"unexpected overwrite")
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        raise AssertionError("The application must mount its CA bundle read-only")


def worker():
    from redis import Redis
    from rq import Worker

    queue = os.environ["DEVFEED_WORKER_QUEUE"]
    # Independent expectations: using worker_queues() here would repeat a routing bug.
    expected = {
        "analysis": [
            "relationships",
            "article-analysis-fresh",
            "article-analysis",
            "topic-analysis",
            "research-verification",
            "source-analysis",
        ],
        "article-analysis": ["article-analysis-fresh", "article-analysis"],
        "article-enrichment": ["article-enrichment-fresh", "article-enrichment"],
    }.get(queue, [queue])
    workers = Worker.all(connection=Redis.from_url(os.environ["DEVFEED_REDIS_URL"]))
    assert len(workers) == 1
    assert workers[0].queue_names() == expected
    assert workers[0].name.startswith("fleet-native-")


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "metadata":
        metadata_setup()
    elif action == "seed":
        seed()
    elif action == "retained":
        retained()
    elif action == "projection":
        projection()
    elif action == "heartbeat":
        heartbeat(sys.argv[2])
    elif action == "permissions":
        permissions()
    elif action == "ca":
        ca()
    elif action == "worker":
        worker()
    elif action == "ready":
        import urllib.request

        with urllib.request.urlopen(sys.argv[2], timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["status"] == "ok"
    elif action == "set-revision":
        with get_engine().begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": sys.argv[2]},
            )
    elif action == "restore-revision":
        with get_engine().begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": SCHEMA_REVISION},
            )
    elif action == "reject-schema":
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlopen(sys.argv[2], timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert json.load(error)["status"] == "migration_required"
        else:
            raise AssertionError("Incompatible schema passed readiness")
    else:
        raise AssertionError("Unknown native Fleet fixture action")
