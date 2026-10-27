"""Current ClickHouse operator capture replaces only disposable PG projections."""

import importlib.util
import uuid

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.analytics_capture import capture_statements, native_capture_statements
from devfeed_core.analytics_schema import BACKFILL_TABLES, TABLES, WATCH_FIELDS
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleOrigin,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceReview,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.support import legacy_migrations
from tests.support.paths import REPO_ROOT

pre_operator_database = legacy_migrations.pre_operator_database
SPEC = importlib.util.spec_from_file_location(
    "operator_cutover_migration",
    REPO_ROOT / "migrations/versions/0042_clickhouse_operator_cards.py",
)
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_capture_contract_is_frozen_and_keeps_private_current_inputs_in_postgres():
    assert native_capture_statements() == tuple(
        migration.capture_statements(migration.SOURCES, migration.WATCH_FIELDS)
    )
    assert capture_statements(migration.SOURCES, migration.WATCH_FIELDS)
    private = {"text", "input_snapshot", "catalog_snapshot", "result", "evidence", "review", "note"}
    for name, spec in TABLES.items():
        assert not private.intersection(spec.fields), name
    assert "text" in WATCH_FIELDS["article_contents"]
    assert "input_snapshot" in WATCH_FIELDS["article_analysis_jobs"]
    assert "review" in WATCH_FIELDS["source_candidates"]
    assert "automation_next_check_at" not in WATCH_FIELDS["articles"]
    assert not {
        "source_candidates",
        "article_contents",
        "source_reviews",
        "source_discovery_jobs",
        "catalog_revisions",
        "article_clocks",
    }.intersection(BACKFILL_TABLES)
    assert len(BACKFILL_TABLES) == 22
    assert "article_clocks" not in TABLES


@pytest.mark.integration
def test_upgrade_keeps_source_history_pending_capture_and_native_cursors(pre_operator_database):
    database = pre_operator_database
    identifier = uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url="https://operator-fixture.invalid/article",
                url_hash=uuid.uuid4().hex,
                title="Keep source business content",
                summary="Private source summary retained in PostgreSQL",
            )
        )
        session.flush()
        session.execute(
            text(
                "UPDATE analytics_pipeline_state SET enabled=true, "
                "native_import_complete=true,native_bootstrap_complete=true,"
                "native_bootstrap_barrier=true,capture_started_at=clock_timestamp(),"
                "lease_token=gen_random_uuid(),lease_until=clock_timestamp()+interval '1h'"
            )
        )
        for version in (1, 2):
            session.execute(
                text(
                    "INSERT INTO analytics_capture_events"
                    "(source_table,operation,record,format_version) "
                    "VALUES('articles','I',jsonb_build_object('id',CAST(:id AS text)),:version)"
                ),
                {"id": str(identifier), "version": version},
            )
        session.execute(
            text("UPDATE analytics_bootstrap_cursors SET complete=true,cursor='{}'::jsonb")
        )
    with database() as session:
        queued = (
            session.execute(text("SELECT * FROM analytics_capture_events ORDER BY id"))
            .mappings()
            .all()
        )
        cursors = (
            session.execute(text("SELECT * FROM analytics_bootstrap_cursors ORDER BY source_table"))
            .mappings()
            .all()
        )
        sequence = session.scalar(
            text("SELECT pg_get_serial_sequence('analytics_capture_events','id')")
        )
        source = session.scalar(
            text("SELECT to_jsonb(a) FROM articles a WHERE id=:id"), {"id": identifier}
        )
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    with database() as session:
        assert (
            session.scalar(
                text("SELECT to_jsonb(a) FROM articles a WHERE id=:id"), {"id": identifier}
            )
            == source
        )
        assert (
            session.execute(text("SELECT * FROM analytics_ingest_events ORDER BY id"))
            .mappings()
            .all()
            == queued
        )
        assert (
            session.execute(text("SELECT * FROM analytics_bootstrap_cursors ORDER BY source_table"))
            .mappings()
            .all()
            == cursors
        )
        assert (
            session.scalar(text("SELECT pg_get_serial_sequence('analytics_ingest_events','id')"))
            == sequence
        )
        state = session.execute(text("SELECT * FROM analytics_pipeline_state")).one()
        assert state.native_bootstrap_complete and state.native_import_complete
        assert state.native_bootstrap_barrier and not state.card_bootstrap_barrier
        assert not state.card_bootstrap_complete
        assert state.native_lease_token is None and state.lease_until is None
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname=current_schema() "
                    "AND tablename IN('pipeline_projections','read_model_events',"
                    "'read_model_states','report_snapshots','report_refreshes')"
                )
            )
            == 0
        )
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgname LIKE 'read_model_%'"
                )
            )
            == 0
        )
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE pronamespace=current_schema()::regnamespace "
                    "AND proname LIKE 'devfeed_read_model_%'"
                )
            )
            == 0
        )
    with database.begin() as session:
        session.execute(
            text("UPDATE articles SET title='A changed card dependency' WHERE id=:id"),
            {"id": identifier},
        )
    with database() as session:
        latest = session.execute(
            text(
                "SELECT * FROM analytics_ingest_events ORDER BY id DESC LIMIT1".replace(
                    "LIMIT1", "LIMIT 1"
                )
            )
        ).one()
        assert latest.id > queued[-1]["id"] and latest.format_version == 3
        assert latest.source_table == "articles" and "title" not in latest.record
        count = session.scalar(text("SELECT count(*) FROM analytics_ingest_events"))
    with database.begin() as session:
        session.execute(text("UPDATE articles SET title=title WHERE id=:id"), {"id": identifier})
        session.execute(
            text("UPDATE articles SET automation_next_check_at=clock_timestamp() WHERE id=:id"),
            {"id": identifier},
        )
    with database() as session:
        assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) == count


@pytest.mark.integration
def test_new_capture_keeps_owners_and_omitted_body_changes_without_private_data(
    pre_operator_database,
):
    database = pre_operator_database
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    first, second, origin_id, candidate_id, discovery_id = [uuid.uuid4() for _ in range(5)]
    source_ids = [uuid.uuid4(), uuid.uuid4()]
    with database.begin() as session:
        session.add_all(
            [
                Article(
                    id=identity,
                    canonical_url=f"https://cards.invalid/{identity}",
                    url_hash=uuid.uuid4().hex,
                    title="Source card",
                )
                for identity in (first, second)
            ]
        )
        session.add_all(
            [
                Source(
                    id=identity,
                    name="Operator source",
                    source_type="publisher",
                    feed_url=f"https://source.invalid/{identity}",
                )
                for identity in source_ids
            ]
        )
        session.flush()
        session.add(
            ArticleOrigin(
                id=origin_id,
                article_id=first,
                source_id=source_ids[0],
                entry_key="owner",
                original_url="https://original.invalid",
            )
        )
        session.add(
            ArticleContent(
                article_id=first,
                url="https://user:secret@private.invalid/path",
                text="PRIVATE BODY",
                content_hash="a" * 64,
                method="fixture",
            )
        )
        session.add(
            SourceCandidate(
                id=candidate_id,
                identity_url="https://candidate.invalid",
                name="Candidate",
                source_id=source_ids[0],
                review={"password": "PRIVATE REVIEW"},
            )
        )
        session.flush()
        session.add(SourceDiscoveryJob(id=discovery_id, candidate_id=candidate_id))
        session.add(
            SourceReview(
                source_id=source_ids[0],
                decision="approved",
                actor="PRIVATE ACTOR",
                note="PRIVATE NOTE",
            )
        )
        session.flush()
        session.execute(text("UPDATE analytics_pipeline_state SET enabled=true"))
    with database.begin() as session:
        session.get(ArticleContent, first).text = "CHANGED PRIVATE BODY"
        session.get(ArticleOrigin, origin_id).article_id = second
        session.get(ArticleOrigin, origin_id).source_id = source_ids[1]
        session.get(SourceCandidate, candidate_id).source_id = source_ids[1]
        session.get(SourceCandidate, candidate_id).review = {"password": "CHANGED PRIVATE REVIEW"}
        session.get(SourceDiscoveryJob, discovery_id).error = "PRIVATE ERROR"
        session.flush()
    with database() as session:
        captured = (
            session.execute(text("SELECT * FROM analytics_ingest_events ORDER BY id"))
            .mappings()
            .all()
        )
        assert all(row["format_version"] == 3 for row in captured)
        assert "PRIVATE" not in str(captured) and "secret@" not in str(captured)
        content = next(row for row in captured if row["source_table"] == "article_contents")
        assert str(content["record"]["article_id"]) == str(first)
        origin = next(row for row in captured if row["source_table"] == "article_origins")
        assert str(origin["before_record"]["article_id"]) == str(first)
        assert str(origin["record"]["article_id"]) == str(second)
        assert str(origin["before_record"]["source_id"]) == str(source_ids[0])
        assert str(origin["record"]["source_id"]) == str(source_ids[1])
        candidate = next(row for row in captured if row["source_table"] == "source_candidates")
        assert str(candidate["before_record"]["source_id"]) == str(source_ids[0])
        assert str(candidate["record"]["source_id"]) == str(source_ids[1])
        assert next(row for row in captured if row["source_table"] == "source_discovery_jobs")[
            "record"
        ]["candidate_id"] == str(candidate_id)


@pytest.mark.integration
def test_native_capture_keeps_delete_truncate_and_rejects_future_versions(pre_operator_database):
    database = pre_operator_database
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    candidate_id = uuid.uuid4()
    with database.begin() as session:
        session.execute(text("UPDATE analytics_pipeline_state SET enabled=true"))
        session.add(
            SourceCandidate(
                id=candidate_id, name="Protocol fixture", identity_url="https://truncate.invalid"
            )
        )
    with database.begin() as session:
        session.delete(session.get(SourceCandidate, candidate_id))
        session.flush()
        session.execute(text("TRUNCATE source_candidates CASCADE"))
        rows = session.execute(
            text(
                "SELECT operation,record,before_record,format_version FROM analytics_ingest_events "
                "WHERE source_table='source_candidates' ORDER BY id"
            )
        ).all()
        assert [row.operation for row in rows] == ["I", "D", "T"]
        assert all(row.format_version == 3 for row in rows)
        assert rows[1].record["id"] == rows[1].before_record["id"] == str(candidate_id)
        assert rows[2].record == {} and rows[2].before_record is None
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO analytics_ingest_events"
                    "(source_table,operation,record,format_version) "
                    "VALUES('articles','I','{}',4)"
                )
            )
