"""Durable boards retain lifecycle semantics without doing lifecycle work on GET."""

from datetime import timedelta

import pytest
from devfeed_admin_api import article_pipeline, pipeline_projection, pipeline_reads, source_pipeline
from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch
from devfeed_core.models import (
    ArticleEnrichmentJob,
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    Source,
    SourceCandidate,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.read_models import pipeline_policy_version
from sqlalchemy import delete, event, func, select, text, update

from tests.admin.articles.test_article_pipeline import make_article
from tests.admin.sources.test_source_pipeline import candidate_for, seed_source

pytestmark = pytest.mark.integration


def comparable(board):
    return {"counts": board["counts"], "columns": board["columns"]}


def test_cold_get_only_reads_and_reports_initialization(admin_client, database, monkeypatch):
    monkeypatch.setattr(article_pipeline, "_board", lambda *a, **k: pytest.fail("Full scan on GET"))
    before = []

    def capture(connection, cursor, statement, parameters, context, many):
        before.append(statement)

    event.listen(database.kw["bind"], "before_cursor_execute", capture)
    try:
        for url in ("/v1/admin/article-pipeline", "/v1/admin/source-pipeline"):
            response = admin_client.get(url)
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "projection_initializing"
    finally:
        event.remove(database.kw["bind"], "before_cursor_execute", capture)
    assert not any(
        "FROM articles" in statement or "FROM article_contents" in statement for statement in before
    )


def test_article_projection_matches_reference_lanes_and_source_groups(admin_client, database):
    with database.begin() as session:
        for stage in (
            "ingested",
            "enrichment",
            "analysis",
            "images",
            "review",
            "ready",
            "published",
            "rejected",
        ):
            make_article(session, state=stage)
        make_article(session, state="ingested", age=90)
        make_article(session, state="published", age=30)
    drain_pipeline(database, batch=3)
    for group in (None, "source"):
        with database() as session:
            expected = article_pipeline._board(
                session, q=None, source_id=None, needs_attention=False, limit=20, group_by=group
            ).model_dump(mode="json")
        response = admin_client.get(
            "/v1/admin/article-pipeline", params={"group_by": group} if group else {}
        )
        assert response.status_code == 200, response.text
        assert comparable(response.json()) == comparable(expected)


def test_source_projection_matches_reference_with_old_active_work(admin_client, database):
    with database.begin() as session:
        source = seed_source(session, age=365)
        candidate_for(session, source)
        candidate_for(session, name="Unlinked", age=100)
    drain_pipeline(database, batch=2)
    query = source_pipeline.SourcePipelineQuery(
        q="", source_type=None, needs_attention=False, terminal_since=None, limit=20
    )
    with database() as session:
        expected = [
            item.model_dump(mode="json")
            for item in source_pipeline._columns(session, query, utcnow())
        ]
    response = admin_client.get("/v1/admin/source-pipeline")
    assert response.status_code == 200, response.text
    assert response.json()["columns"] == expected


def test_get_survives_stopped_projector_and_does_not_read_evidence(
    admin_client, database, monkeypatch
):
    with database.begin() as session:
        article = make_article(session, state="ingested")
        identifier = article.id
    drain_pipeline(database)
    with database.begin() as session:
        session.execute(
            text("UPDATE articles SET title='A newer title' WHERE id=:id"), {"id": identifier}
        )
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(database.kw["bind"], "before_cursor_execute", capture)
    try:
        response = admin_client.get("/v1/admin/article-pipeline")
    finally:
        event.remove(database.kw["bind"], "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "refreshing"
    assert not any(
        "FROM articles" in sql or "FROM article_analysis_jobs" in sql for sql in statements
    )
    drain_pipeline(database)
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.json()["columns"][0]["items"][0]["title"] == "A newer title"


def test_publication_and_projection_ack_roll_back_together(database, monkeypatch):
    with database.begin() as session:
        make_article(session, state="ingested")

    def crash(*args, **kwargs):
        raise RuntimeError("publisher crashed before acknowledgement")

    original = pipeline_projection.acknowledge_events
    monkeypatch.setattr(pipeline_projection, "acknowledge_events", crash)
    with pytest.raises(RuntimeError, match="publisher crashed"):
        project_batch(database)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PipelineProjection)) == 0
        assert session.scalar(select(func.count()).select_from(ReadModelEvent)) > 0
    monkeypatch.setattr(pipeline_projection, "acknowledge_events", original)
    drain_pipeline(database)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PipelineProjection)) == 2


def test_retry_clock_transition_is_projected_without_an_editorial_write(database):
    now = utcnow()
    with database.begin() as session:
        article = make_article(session, state="failed_enrichment")
        job = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == article.id)
        )
        job.status = "queued"
        job.attempts = 1
        job.available_at = now + timedelta(seconds=10)
        identifier = article.id
    drain_pipeline(database)
    policy = pipeline_policy_version()
    with database() as session:
        assert (
            session.get(PipelineProjection, (policy, "article", identifier)).state == "retry_wait"
        )
    project_batch(database, now=now + timedelta(seconds=11))
    with database() as session:
        assert (
            session.get(PipelineProjection, (policy, "article", identifier)).state != "retry_wait"
        )


def test_policy_rebuild_retains_previous_generation_until_complete(
    database, admin_client, monkeypatch
):
    with database.begin() as session:
        for _ in range(4):
            make_article(session, state="ingested")
    drain_pipeline(database)
    previous = pipeline_policy_version()
    monkeypatch.setattr(pipeline_projection, "pipeline_policy_version", lambda: "next-policy")
    monkeypatch.setattr(pipeline_reads, "pipeline_policy_version", lambda: "next-policy")
    project_batch(database, batch=1)
    with database() as session:
        assert session.get(ReadModelState, "pipeline").payload["serving_policy"] == previous
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.policy_version == previous)
            )
            > 0
        )
    for url in ("/v1/admin/article-pipeline", "/v1/admin/source-pipeline"):
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.json()["snapshot"]["status"] == "rebuilding"
        assert response.json()["snapshot"]["generation"] == previous
    drain_pipeline(database, batch=2)
    with database() as session:
        assert session.get(ReadModelState, "pipeline").payload["serving_policy"] == "next-policy"
    for url in ("/v1/admin/article-pipeline", "/v1/admin/source-pipeline"):
        snapshot = admin_client.get(url).json()["snapshot"]
        assert snapshot["status"] == "fresh" and snapshot["generation"] == "next-policy"


def test_global_rebuild_removes_entities_lost_to_truncate(database):
    with database.begin() as session:
        make_article(session, state="ingested")
    drain_pipeline(database)
    with database.begin() as session:
        session.execute(text("TRUNCATE articles CASCADE"))
    drain_pipeline(database, batch=1)
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 0
        )


def test_backfill_reuses_catalog_across_batches_without_a_redis_cache(database, monkeypatch):
    from devfeed_core import analysis
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    with database.begin() as session:
        for _ in range(6):
            make_article(session, state="ready")
    reads = []
    original = analysis._read_catalog

    def load(session):
        reads.append(True)
        return original(session)

    monkeypatch.setattr(analysis, "_read_catalog", load)
    drain_pipeline(database, batch=2)
    assert len(reads) == 1
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 6
        )


def test_projected_search_preserves_normalization_literals_and_field_boundaries(
    admin_client, database
):
    with database.begin() as session:
        for title, ai_title in (
            ("PostgreSQL—C++ 100% _storage", "Kubernetes security"),
            ("queue", "runtime"),
            ("Код 解析 Python", "deep  work"),
        ):
            article = make_article(session, state="ingested")
            article.title, article.ai_title = title, ai_title
    drain_pipeline(database, batch=2)
    for query in (
        "C++",
        "100%",
        "_",
        "КОД",
        "解析",
        "PostgreSQL C++",
        "queue runtime",
        "deep work",
        "%",
        "—",
        "  ",
    ):
        with database() as session:
            expected = article_pipeline._board(
                session,
                q=query,
                source_id=None,
                needs_attention=False,
                limit=20,
            ).model_dump(mode="json")
        response = admin_client.get("/v1/admin/article-pipeline", params={"q": query})
        assert response.status_code == 200, response.text
        assert comparable(response.json()) == comparable(expected), query
        if query == "queue runtime":
            assert not any(response.json()["counts"].values())


def test_grouped_board_reads_all_stages_in_one_bounded_query(admin_client, database):
    with database.begin() as session:
        for stage in ("ingested", "analysis", "published", "rejected"):
            make_article(session, state=stage)
    drain_pipeline(database)
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    engine = database.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = admin_client.get("/v1/admin/article-pipeline", params={"group_by": "source"})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert len(statements) <= 6
    assert len(response.json()["columns"]) == 8
    assert not any("FROM articles" in statement for statement in statements)


def test_search_keeps_working_with_legacy_search_text_during_policy_rebuild(
    admin_client, database, monkeypatch
):
    from devfeed_admin_api import pipeline_reads

    with database.begin() as session:
        article = make_article(session, state="ingested")
        article.title = "Kubernetes—API operations"
    drain_pipeline(database)
    with database.begin() as session:
        for row in session.scalars(select(PipelineProjection)):
            row.search_text = row.payload["title"] if row.kind == "article" else ""
    monkeypatch.setattr(pipeline_projection, "pipeline_policy_version", lambda: "new-search-policy")
    monkeypatch.setattr(pipeline_reads, "pipeline_policy_version", lambda: "new-search-policy")
    project_batch(database, batch=1)
    before = admin_client.get("/v1/admin/article-pipeline", params={"q": "kubernetes api"})
    assert before.status_code == 200
    assert before.json()["snapshot"]["status"] == "rebuilding"
    assert sum(before.json()["counts"].values()) == 1
    drain_pipeline(database, batch=1)
    after = admin_client.get("/v1/admin/article-pipeline", params={"q": "kubernetes api"})
    assert after.status_code == 200
    assert after.json()["snapshot"]["status"] == "fresh"
    assert comparable(before.json()) == comparable(after.json())


@pytest.mark.parametrize(
    "change", ["rename_topic", "delete_topic", "new_matching_topic", "new_matching_tag"]
)
def test_catalog_refresh_preserves_live_article_readiness_after_taxonomy_changes(
    admin_client, database, monkeypatch, change
):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        article = make_article(session)
        identifier = article.id
    drain_pipeline(database)
    before = admin_client.get("/v1/admin/article-pipeline").json()
    assert before["counts"]["ready"] == 1
    with database.begin() as session:
        if change == "rename_topic":
            session.execute(
                update(Topic).values(name="Rust compiler", aliases=["Rust"], slug="rust-compiler")
            )
        elif change == "delete_topic":
            session.execute(delete(Topic))
        elif change == "new_matching_topic":
            session.add(
                Topic(
                    name="Task tracing",
                    slug="task-tracing",
                    aliases=["Python"],
                    kind="tool",
                    status="active",
                )
            )
        else:
            session.add(Tag(name="Python tracing", slug="python-tracing", aliases=["Python"]))
    drain_pipeline(database, batch=1)
    with database() as session:
        expected = article_pipeline._board(
            session, q=None, source_id=None, needs_attention=False, limit=20
        ).model_dump(mode="json")
        stored = session.get(PipelineProjection, (pipeline_policy_version(), "article", identifier))
        assert stored.payload["_catalog_sensitive"]
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "fresh"
    assert comparable(response.json()) == comparable(expected)
    assert comparable(response.json()) != comparable(before)
    assert response.json()["counts"]["ready"] == 0


def test_source_freshness_and_updates_are_independent_of_article_catalog_catchup(
    admin_client, database
):
    with database.begin() as session:
        source = seed_source(session, approval="approved", enabled=True)
        source_id = source.id
        candidate = candidate_for(session)
        candidate_id = candidate.id
        for _ in range(8):
            make_article(session, source=source)
    drain_pipeline(database, batch=2)
    before = admin_client.get("/v1/admin/source-pipeline").json()
    assert before["snapshot"]["status"] == "fresh"
    with database.begin() as session:
        session.add(Tag(name="New catalog item", slug="new-catalog-item"))
    project_batch(database, batch=1)
    article_board = admin_client.get("/v1/admin/article-pipeline").json()
    source_board = admin_client.get("/v1/admin/source-pipeline").json()
    assert article_board["snapshot"]["status"] == "refreshing"
    assert source_board["snapshot"]["status"] == "fresh"
    assert source_board["snapshot"]["generated_at"] == before["snapshot"]["generated_at"]
    assert source_board["snapshot"]["generation"] == before["snapshot"]["generation"]
    assert comparable(source_board) == comparable(before)
    with database.begin() as session:
        session.execute(update(Source).where(Source.id == source_id).values(name="Renamed source"))
        session.execute(
            update(SourceCandidate)
            .where(SourceCandidate.id == candidate_id)
            .values(name="Renamed candidate")
        )
    assert (
        admin_client.get("/v1/admin/source-pipeline").json()["snapshot"]["status"] == "refreshing"
    )
    # New row events must be handled without waiting for article catalog coverage.
    for _ in range(30):
        project_batch(database, batch=1)
        current = admin_client.get("/v1/admin/source-pipeline").json()
        if current["snapshot"]["status"] == "fresh":
            assert (
                admin_client.get("/v1/admin/article-pipeline").json()["snapshot"]["status"]
                == "refreshing"
            )
            break
    else:
        pytest.fail("Source updates did not become fresh independently of article catchup")
    cards = [item for column in current["columns"] for item in column["items"]]
    assert {item["name"] for item in cards} == {"Renamed source", "Renamed candidate"}
    assert current["snapshot"]["generated_at"] > before["snapshot"]["generated_at"]
    drain_pipeline(database, batch=2)
    assert admin_client.get("/v1/admin/article-pipeline").json()["snapshot"]["status"] == "fresh"


def test_catalog_refresh_updates_recent_published_retry_actions_without_unpublishing(
    admin_client, database
):
    with database.begin() as session:
        identifier = make_article(session, state="published").id
    drain_pipeline(database)
    before = admin_client.get("/v1/admin/article-pipeline").json()
    published_before = next(
        column for column in before["columns"] if column["stage"] == "published"
    )
    assert published_before["items"][0]["retry_targets"] == ["enrichment", "images"]
    with database.begin() as session:
        session.execute(update(Topic).values(name="Rust compiler", aliases=["Rust"]))
    drain_pipeline(database, batch=1)
    with database() as session:
        expected = article_pipeline._board(
            session, q=None, source_id=None, needs_attention=False, limit=20
        ).model_dump(mode="json")
        assert session.get(PipelineProjection, (pipeline_policy_version(), "article", identifier))
    after = admin_client.get("/v1/admin/article-pipeline").json()
    assert comparable(after) == comparable(expected)
    assert after["counts"]["published"] == 1
    published_after = next(column for column in after["columns"] if column["stage"] == "published")
    assert published_after["items"][0]["id"] == str(identifier)
    assert published_after["items"][0]["retry_targets"] == []
