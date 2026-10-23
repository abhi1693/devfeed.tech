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
        required = {
            report_key(group, days)
            for group in REPORT_GROUPS
            for days in ((1,) if group in INDEPENDENT_GROUPS else (7, 30))
        }
        assert len(REPORT_GROUPS) == 16 and len(required) == 27
        for key in required:
            report = session.get(ReportSnapshot, key)
            assert report is not None and report.policy_version == report_policy_version()
    retained()
    print("Native backfill retained records and published all 27 current report snapshots")


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
