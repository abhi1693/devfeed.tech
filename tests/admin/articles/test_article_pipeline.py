"""Pipeline visibility and recovery use the publication admission contract."""

import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api.pipeline_projection import drain_pipeline
from devfeed_core.analysis import (
    PROMPT_VERSION,
    analysis_candidates,
    catalog,
    snapshot_hash,
    source_snapshot,
)
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTopic,
    Source,
    Topic,
    utcnow,
)
from sqlalchemy import event, func, select

from tests.admin.auth.test_auth import oidc_app as oidc_app


def make_article(session, *, state="ready", source=None, age=0, title="Python task tracing"):
    source = source or Source(
        name="Python publisher",
        source_type="publisher",
        feed_url=f"https://publisher.example/{uuid.uuid4()}/feed",
        approval_status="approved",
        enabled=True,
    )
    topic = session.scalar(select(Topic).where(Topic.name == "Python runtime"))
    if topic is None:
        topic = Topic(
            name="Python runtime",
            slug="python-runtime",
            kind="language",
            aliases=["Python"],
            status="active",
        )
        session.add(topic)
        session.flush()
    article = Article(
        canonical_url=f"https://publisher.example/posts/{uuid.uuid4()}",
        url_hash=uuid.uuid4().hex,
        title=title,
        summary="Trace Python tasks and inspect their runtime state.",
        ai_summary="Inspect task state to diagnose a blocked Python coroutine.",
        ai_description="Trace Python tasks and inspect blocked coroutines.",
        review_status="approved",
        publication_status="unpublished",
        language="en",
        classification_provenance={"developer_relevance": "relevant", "page_kind": "article"},
        discovered_at=utcnow() - timedelta(days=age),
    )
    article.origins = [
        ArticleOrigin(source=source, entry_key=uuid.uuid4().hex, original_url=article.canonical_url)
    ]
    article.topic_links = [
        ArticleTopic(topic=topic, role="primary", relevance=1, evidence="Python", origin="manual")
    ]
    session.add(article)
    session.flush()
    if state == "ingested":
        return article
    enrichment = ArticleEnrichmentJob(
        article_id=article.id,
        status="succeeded",
        outcome="enriched",
        result={"input_url": article.canonical_url, "image_url": None},
        finished_at=utcnow(),
    )
    session.add(enrichment)
    if state == "enrichment":
        enrichment.status = "running"
        return article
    if state == "failed_enrichment":
        enrichment.status = "failed"
        enrichment.error = "Publisher temporarily unavailable"
        return article
    snapshot = source_snapshot(article, None)
    candidates = analysis_candidates(catalog(session), snapshot)
    analysis = ArticleAnalysisJob(
        article_id=article.id,
        status="succeeded",
        outcome="applied",
        input_snapshot=snapshot,
        input_hash=snapshot_hash(snapshot),
        catalog_snapshot=candidates,
        catalog_hash=snapshot_hash(candidates),
        editorial_revision=article.editorial_revision,
        prompt_version=PROMPT_VERSION,
        result={"outcome": "ready", "developer_relevance": "relevant", "page_kind": "article"},
        finished_at=utcnow(),
    )
    session.add(analysis)
    session.flush()
    article.classification_provenance = {
        **article.classification_provenance,
        "analysis_id": str(analysis.id),
        "input_hash": analysis.input_hash,
    }
    if state == "analysis":
        analysis.status = "running"
    elif state == "images":
        article.image_url = "https://publisher.example/image.png"
        session.add(ArticleImageJob(article_id=article.id, status="running", operation="discover"))
    elif state == "review":
        article.review_status = "pending"
    elif state == "published":
        article.publication_status = "published"
        article.published_to_feed_at = utcnow() - timedelta(days=age)
    elif state == "rejected":
        article.review_status = "rejected"
        session.add(
            ArticleReview(
                article_id=article.id,
                action="reject",
                revision=0,
                created_at=utcnow(),
                note="Out of scope",
            )
        )
    return article


def test_source_grouping_requires_admin_before_database_access(oidc_app):
    for path in (
        "/v1/admin/article-pipeline?group_by=source",
        "/v1/admin/article-pipeline/enrichment?group_by=source",
        "/v1/admin/article-pipeline/enrichment?source_group=unknown",
    ):
        assert oidc_app.client.get(path).status_code == 401


@pytest.mark.integration
def test_board_exact_lanes_filters_and_private_projection(admin_client, database):
    with database.begin() as session:
        records = {
            state: make_article(session, state=state)
            for state in (
                "ingested",
                "enrichment",
                "analysis",
                "images",
                "review",
                "ready",
                "published",
                "rejected",
            )
        }
        identifiers = {state: str(article.id) for state, article in records.items()}
        source_id = str(records["ready"].origins[0].source_id)
        make_article(session, state="published", age=30)
        old_pending = make_article(session, state="ingested", age=30)
        old_id = str(old_pending.id)
    drain_pipeline(database)
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.status_code == 200, response.text
    board = response.json()
    assert board["counts"] == {
        "ingested": 2,
        "enrichment": 1,
        "analysis": 1,
        "images": 1,
        "review": 1,
        "ready": 1,
        "published": 1,
        "rejected": 1,
    }
    columns = {column["stage"]: column for column in board["columns"]}
    for stage, identifier in identifiers.items():
        assert identifier in [card["id"] for card in columns[stage]["items"]]
    assert old_id in [card["id"] for card in columns["ingested"]["items"]]
    assert "input_snapshot" not in response.text
    assert "catalog_snapshot" not in response.text
    assert "source_metadata" not in response.text
    filtered = admin_client.get(
        "/v1/admin/article-pipeline", params={"source_id": source_id}
    ).json()
    assert sum(filtered["counts"].values()) == 1
    assert filtered["counts"]["ready"] == 1
    attention = admin_client.get(
        "/v1/admin/article-pipeline", params={"needs_attention": "true"}
    ).json()
    assert sum(attention["counts"].values()) == 0


@pytest.mark.integration
def test_detail_fallback_and_terminal_publish_actions(admin_client, database):
    with database.begin() as session:
        identifiers = {
            state: str(make_article(session, state=state).id)
            for state in ("ready", "rejected", "published", "ingested")
        }
    ready = admin_client.get(f"/v1/admin/articles/{identifiers['ready']}/pipeline").json()
    assert ready["can_publish"] is True
    assert all(check["status"] == "passed" for check in ready["checks"])
    assert {"overview", "description", "primary_topic", "approval"} <= {
        check["code"] for check in ready["checks"]
    }
    assert (
        next(check for check in ready["checks"] if check["code"] == "images")["status"] == "passed"
    )
    for stage in ("rejected", "published"):
        detail = admin_client.get(f"/v1/admin/articles/{identifiers[stage]}/pipeline").json()
        assert detail["can_publish"] is False
        assert detail["allowed_actions"] == []
    ingested = admin_client.get(f"/v1/admin/articles/{identifiers['ingested']}/pipeline").json()
    assert ingested["can_publish"] is False
    assert ingested["allowed_actions"] == ["advance"]


@pytest.mark.integration
def test_pipeline_paging_no_per_card_queries(admin_client, database):
    with database.begin() as session:
        for _ in range(12):
            make_article(session, state="ingested")
        engine = session.bind
    drain_pipeline(database, batch=5)
    queries = []

    def observe(*args):
        queries.append(args[2])

    event.listen(engine, "before_cursor_execute", observe)
    try:
        first = admin_client.get("/v1/admin/article-pipeline/ingested?limit=4").json()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert first["total"] == 12
    assert first["next_cursor"] == 4
    assert len(first["items"]) == 4
    assert len(queries) <= 6
    assert not any(
        "article_contents" in query or "article_analysis_jobs" in query for query in queries
    )
    second = admin_client.get("/v1/admin/article-pipeline/ingested?limit=4&cursor=4").json()
    assert not {card["id"] for card in first["items"]} & {card["id"] for card in second["items"]}
    assert second["next_cursor"] == 8


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["enrichment", "analysis", "images"])
def test_running_articles_precede_other_states_across_batches_and_pages(
    admin_client, database, monkeypatch, stage
):
    from devfeed_admin_api import article_pipeline

    monkeypatch.setattr(article_pipeline, "BATCH_SIZE", 2)
    base = utcnow() - timedelta(days=1)
    rows = []
    with database.begin() as session:
        for state, seconds in (
            ("blocked", 0),
            ("queued", 1),
            ("retry_wait", 1),
            ("running", 2),
            ("queued", 3),
            ("running", 4),
            ("running", 4),
        ):
            article = make_article(session, state=stage)
            article.discovered_at = base + timedelta(seconds=seconds)
            model = {
                "enrichment": ArticleEnrichmentJob,
                "analysis": ArticleAnalysisJob,
                "images": ArticleImageJob,
            }[stage]
            job = session.scalar(select(model).where(model.article_id == article.id))
            job.status = (
                "failed" if state == "blocked" else "running" if state == "running" else "queued"
            )
            job.attempts = 1 if state == "retry_wait" else 0
            job.available_at = (
                utcnow() + timedelta(minutes=1) if state == "retry_wait" else utcnow()
            )
            if stage == "images":
                article.image_url = None
                job.storage = {"input_url": article.canonical_url}
            rows.append((state != "running", article.discovered_at, article.id))
    expected = [str(identifier) for _, _, identifier in sorted(rows)]
    drain_pipeline(database)
    board_response = admin_client.get("/v1/admin/article-pipeline?limit=2")
    assert board_response.status_code == 200, board_response.text
    board_column = next(
        column for column in board_response.json()["columns"] if column["stage"] == stage
    )
    assert board_column["total"] == len(rows)
    assert [card["id"] for card in board_column["items"]] == expected[:2]
    assert all(card["state"] == "running" for card in board_column["items"])
    actual, cursor = [], 0
    while cursor is not None:
        response = admin_client.get(
            f"/v1/admin/article-pipeline/{stage}", params={"limit": 2, "cursor": cursor}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == len(rows)
        actual.extend(card["id"] for card in page["items"])
        if cursor == 0:
            assert page["items"] == board_column["items"]
        cursor = page["next_cursor"]
    assert actual == expected and len(set(actual)) == len(rows)
    repeated = admin_client.get(
        f"/v1/admin/article-pipeline/{stage}", params={"limit": 2, "cursor": 2}
    ).json()
    assert [card["id"] for card in repeated["items"]] == expected[2:4]


@pytest.mark.integration
def test_source_groups_count_complete_columns_before_group_pagination(
    admin_client, database, monkeypatch
):
    from devfeed_admin_api import article_pipeline

    monkeypatch.setattr(article_pipeline, "BATCH_SIZE", 2)
    with database.begin() as session:
        sources = {
            name: Source(
                name=name,
                source_type="publisher",
                feed_url=f"https://{key}.example/feed",
                approval_status="approved",
            )
            for key, name in (
                ("a", "A waiting"),
                ("b", "B attention"),
                ("z", "Z running"),
            )
        }
        for name, states in (
            ("A waiting", ["queued", "queued"]),
            ("B attention", ["failed", "retry_wait"]),
            ("Z running", ["running", "queued", "running"]),
        ):
            for state in states:
                article = make_article(session, state="enrichment", source=sources[name], age=30)
                job = session.scalar(
                    select(ArticleEnrichmentJob).where(
                        ArticleEnrichmentJob.article_id == article.id
                    )
                )
                job.status = "queued" if state == "retry_wait" else state
                job.attempts = 1 if state == "retry_wait" else 0
                job.available_at = (
                    utcnow() + timedelta(minutes=10) if state == "retry_wait" else utcnow()
                )
        identifiers = {name: str(source.id) for name, source in sources.items()}
    drain_pipeline(database)
    response = admin_client.get(
        "/v1/admin/article-pipeline", params={"group_by": "source", "limit": 1}
    )
    assert response.status_code == 200, response.text
    board = response.json()
    initial = next(column for column in board["columns"] if column["stage"] == "enrichment")
    assert board["counts"]["enrichment"] == initial["total"] == 7
    assert initial["items"] == [] and initial["next_cursor"] == 1
    assert initial["source_groups"] == [
        {
            "source_id": identifiers["Z running"],
            "source_name": "Z running",
            "total": 3,
            "state_counts": {"running": 2, "waiting": 1},
        }
    ]
    groups, cursor = [], 0
    while cursor is not None:
        response = admin_client.get(
            "/v1/admin/article-pipeline/enrichment",
            params={"group_by": "source", "limit": 1, "cursor": cursor},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 7 and page["items"] == []
        groups.extend(page["source_groups"])
        cursor = page["next_cursor"]
    assert [group["source_name"] for group in groups] == [
        "Z running",
        "B attention",
        "A waiting",
    ]
    assert groups[1]["state_counts"] == {"blocked": 1, "retry_wait": 1}
    assert len({group["source_id"] for group in groups}) == 3
    attention = admin_client.get(
        "/v1/admin/article-pipeline/enrichment",
        params={"group_by": "source", "needs_attention": True},
    ).json()
    assert attention["total"] == 2
    assert attention["source_groups"] == [groups[1]]
    normal = admin_client.get("/v1/admin/article-pipeline/enrichment?limit=1").json()
    assert normal["source_groups"] == [] and normal["items"][0]["state"] == "running"
    expanded, cursor = [], 0
    while cursor is not None:
        page = admin_client.get(
            "/v1/admin/article-pipeline/enrichment",
            params={"source_group": identifiers["Z running"], "limit": 1, "cursor": cursor},
        ).json()
        assert page["total"] == 3
        expanded.extend(page["items"])
        cursor = page["next_cursor"]
    assert [card["state"] for card in expanded] == ["running", "running", "waiting"]
    assert len({card["id"] for card in expanded}) == 3


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["ingested", "published", "rejected"])
def test_group_expansion_uses_displayed_source_with_multi_origin_unknown_and_filters(
    admin_client, database, stage
):
    with database.begin() as session:
        first = Source(
            name="A approved",
            source_type="publisher",
            feed_url="https://first.example/feed",
            approval_status="approved",
        )
        second = Source(
            name="B approved",
            source_type="publisher",
            feed_url="https://second.example/feed",
            approval_status="approved",
        )
        rejected = Source(
            name="0 rejected",
            source_type="publisher",
            feed_url="https://rejected.example/feed",
            approval_status="rejected",
        )
        multi = make_article(session, state=stage, source=first, title="Grouped Python multi")
        multi.origins.extend(
            ArticleOrigin(
                source=source, entry_key=uuid.uuid4().hex, original_url=multi.canonical_url
            )
            for source in (second, rejected)
        )
        own = make_article(session, state=stage, source=second, title="Grouped Python own")
        unknown = make_article(session, state=stage, title="Grouped Python unknown")
        for origin in unknown.origins:
            session.delete(origin)
        make_article(session, state=stage, source=second, title="Excluded title")
        if stage != "ingested":
            old = make_article(
                session, state=stage, source=second, age=30, title="Grouped Python old"
            )
            if stage == "rejected":
                session.scalar(
                    select(ArticleReview).where(ArticleReview.article_id == old.id)
                ).created_at = utcnow() - timedelta(days=30)
        session.flush()
        first_id, second_id = str(first.id), str(second.id)
        multi_id, own_id, unknown_id = str(multi.id), str(own.id), str(unknown.id)
    params = {"group_by": "source", "q": "Grouped Python"}
    drain_pipeline(database)
    response = admin_client.get(f"/v1/admin/article-pipeline/{stage}", params=params)
    assert response.status_code == 200, response.text
    column = response.json()
    assert column["total"] == 3
    assert [(group["source_id"], group["total"]) for group in column["source_groups"]] == [
        (first_id, 1),
        (second_id, 1),
        (None, 1),
    ]
    expected_state = "waiting" if stage == "ingested" else "complete"
    assert all(group["state_counts"] == {expected_state: 1} for group in column["source_groups"])
    for group, expected in ((first_id, multi_id), (second_id, own_id), ("unknown", unknown_id)):
        expanded = admin_client.get(
            f"/v1/admin/article-pipeline/{stage}",
            params={"q": "Grouped Python", "source_group": group},
        )
        assert expanded.status_code == 200, expanded.text
        page = expanded.json()
        assert page["total"] == 1 and page["next_cursor"] is None
        assert [card["id"] for card in page["items"]] == [expected]
    # The existing source filter includes any origin; group expansion must still
    # distinguish the displayed primary source from a secondary origin.
    filtered = admin_client.get(
        f"/v1/admin/article-pipeline/{stage}",
        params={**params, "source_id": second_id},
    ).json()
    assert filtered["total"] == 2
    assert [group["source_id"] for group in filtered["source_groups"]] == [first_id, second_id]
    expanded = admin_client.get(
        f"/v1/admin/article-pipeline/{stage}",
        params={"q": "Grouped Python", "source_id": second_id, "source_group": first_id},
    ).json()
    assert [card["id"] for card in expanded["items"]] == [multi_id]


@pytest.mark.integration
def test_resume_revision_guard_and_terminal_protection(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="ingested")
        identifier = str(article.id)
        published = str(make_article(session, state="published").id)
    path = f"/v1/admin/articles/{identifier}/pipeline/advance"
    assert admin_client.post(path, json={"expected_revision": 99}).status_code == 409
    resumed = admin_client.post(path, json={"expected_revision": 0})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["stage"] == "enrichment"
    assert resumed.json()["jobs"]["enrichment"]["status"] == "queued"
    assert admin_client.post(path, json={"expected_revision": 0}).status_code == 409
    assert (
        admin_client.post(
            f"/v1/admin/articles/{published}/pipeline/advance", json={"expected_revision": 0}
        ).status_code
        == 409
    )
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleEnrichmentJob)
                .where(ArticleEnrichmentJob.article_id == uuid.UUID(identifier))
            )
            == 1
        )


@pytest.mark.integration
def test_failed_waiting_articles_are_visible_and_not_automatically_retried(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="failed_enrichment", age=40)
        identifier = str(article.id)
    drain_pipeline(database)
    board = admin_client.get("/v1/admin/article-pipeline?needs_attention=true").json()
    column = next(column for column in board["columns"] if column["stage"] == "enrichment")
    assert column["total"] == 1
    card = column["items"][0]
    assert card["id"] == identifier and card["state"] == "blocked"
    assert card["jobs"]["enrichment"]["retryable"] is True
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert detail["can_publish"] is False
    assert detail["allowed_actions"] == []


@pytest.mark.integration
def test_projected_board_avoids_cold_evidence_reads_and_updates_after_worker(
    admin_client, database
):
    from devfeed_admin_api import article_pipeline

    with article_pipeline._readiness_lock:
        article_pipeline._readiness_cache.clear()
    with database.begin() as session:
        article = make_article(session)
        identifier = article.id
        session.add(
            ArticleContent(
                article_id=identifier,
                url=article.canonical_url,
                text=article.summary,
                method="description",
                content_hash="evidence-v1",
            )
        )
        # Identical summary evidence keeps the original input identity current.
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
        )
        session.flush()
        snapshot = source_snapshot(article, session.get(ArticleContent, identifier))
        job.input_snapshot = snapshot
        job.input_hash = snapshot_hash(snapshot)
        article.classification_provenance = {
            **article.classification_provenance,
            "input_hash": job.input_hash,
        }
        engine = session.bind
    drain_pipeline(database)
    assert admin_client.get("/v1/admin/article-pipeline").json()["counts"]["ready"] == 1
    with article_pipeline._readiness_lock:
        article_pipeline._readiness_cache.clear()
    queries = []

    def observe(*args):
        queries.append(args[2])

    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert admin_client.get("/v1/admin/article-pipeline").json()["counts"]["ready"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not any("article_contents.text" in query for query in queries)
    assert not any("article_analysis_jobs.input_snapshot" in query for query in queries)
    with database.begin() as session:
        session.get(Article, identifier).ai_description = None
    drain_pipeline(database)
    changed = admin_client.get("/v1/admin/article-pipeline").json()
    assert changed["counts"]["ready"] == 0 and changed["counts"]["analysis"] == 1
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.ai_description = "Inspect Python coroutine state."
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
        )
        job.status = "failed"
        job.error = "Analysis failed"
    drain_pipeline(database)
    failed = admin_client.get("/v1/admin/article-pipeline?needs_attention=true").json()
    assert failed["counts"]["analysis"] == 1
    card = next(column for column in failed["columns"] if column["stage"] == "analysis")["items"][0]
    assert card["state"] == "blocked" and "analysis_failed" in card["blockers"]


@pytest.mark.integration
def test_active_old_run_wins_over_newer_terminal_history(admin_client, database):
    with database.begin() as session:
        article = make_article(session)
        identifier = str(article.id)
        active = ArticleAnalysisJob(
            article_id=article.id, status="running", created_at=utcnow() - timedelta(days=1)
        )
        session.add(active)
        session.flush()
        active_id = str(active.id)
    drain_pipeline(database)
    board = admin_client.get("/v1/admin/article-pipeline").json()
    assert board["counts"]["ready"] == 0 and board["counts"]["analysis"] == 1
    card = next(column for column in board["columns"] if column["stage"] == "analysis")["items"][0]
    assert card["jobs"]["analysis"]["id"] == active_id and card["state"] == "running"
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert detail["can_publish"] is False
    assert "analysis_pending" in detail["blockers"]


@pytest.mark.integration
def test_cutoff_changes_invalidate_warm_counts_and_prevent_noop_resume(
    admin_client, database, monkeypatch
):
    from datetime import UTC, datetime

    from devfeed_core.config import get_settings

    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_AI_CONTENT_NOT_BEFORE", "2026-10-01")
    get_settings.cache_clear()
    with database.begin() as session:
        article = make_article(session)
        article.published_at = datetime(2026, 10, 18, tzinfo=UTC)
        identifier = str(article.id)
    drain_pipeline(database)
    assert admin_client.get("/v1/admin/article-pipeline").json()["counts"]["ready"] == 1
    with database.begin() as session:
        session.get(Article, uuid.UUID(identifier)).published_at = datetime(2026, 9, 30, tzinfo=UTC)
        no_analysis = make_article(session, state="enrichment")
        no_analysis.published_at = datetime(2026, 9, 30, tzinfo=UTC)
        job = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == no_analysis.id)
        )
        job.status = "succeeded"
        no_analysis_id = str(no_analysis.id)
    drain_pipeline(database)
    board = admin_client.get("/v1/admin/article-pipeline").json()
    assert board["counts"]["ready"] == 0 and board["counts"]["analysis"] == 2
    detail = admin_client.get(f"/v1/admin/articles/{no_analysis_id}/pipeline").json()
    assert "content_date_deferred" in detail["blockers"]
    assert detail["allowed_actions"] == []
    assert (
        admin_client.post(
            f"/v1/admin/articles/{no_analysis_id}/pipeline/advance", json={"expected_revision": 0}
        ).status_code
        == 409
    )


@pytest.mark.integration
def test_missing_source_evidence_offers_explicit_refetch_only(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="enrichment")
        article.summary = ""
        identifier = str(article.id)
        job = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == article.id)
        )
        job.status = "succeeded"
        job.outcome = "not_found"
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert "insufficient_source_text" in detail["blockers"]
    assert detail["advance_label"] == "Fetch source again"
    assert detail["can_publish"] is False
    resumed = admin_client.post(
        f"/v1/admin/articles/{identifier}/pipeline/advance", json={"expected_revision": 0}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["jobs"]["enrichment"]["status"] == "queued"
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleEnrichmentJob)
                .where(ArticleEnrichmentJob.article_id == uuid.UUID(identifier))
            )
            == 2
        )


@pytest.mark.integration
def test_pipeline_views_release_connection_before_response_validation(database):
    from devfeed_admin_api import article_pipeline

    with database.begin() as session:
        identifier = make_article(session).id
    drain_pipeline(database)
    with database() as session:
        article_pipeline.board(session, q=None, source_id=None, needs_attention=False, limit=20)
        assert not session.in_transaction()
        article_pipeline.column(
            "ready", session, q=None, source_id=None, needs_attention=False, limit=20, cursor=0
        )
        assert not session.in_transaction()
        result = article_pipeline.detail(identifier, session)
        assert not session.in_transaction()
        assert result.can_publish is True


@pytest.mark.integration
def test_pipeline_exposes_specialist_progress_without_private_results(admin_client, database):
    from devfeed_core.article_analysis_steps import STEP_SPECS

    with database.begin() as session:
        article = make_article(session, state="analysis")
        identifier = str(article.id)
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article.id)
        )
        job.status = "queued"
        job.attempts = 2
        job.available_at = utcnow() + timedelta(minutes=2)
        job.active_step = "overview"
        records = {}
        for name, spec in STEP_SPECS.items():
            records[name] = {
                "status": "pending"
                if name in {"overview", "description"}
                else "skipped"
                if name == "identifying_title"
                else "succeeded",
                "prompt_version": spec.prompt_version,
                "attempts": 2 if name == "overview" else 0 if name == "identifying_title" else 1,
                "output": {"private": "DO_NOT_EXPOSE_DRAFT_OUTPUT"},
                "input_hash": "PRIVATE_CONTEXT_IDENTITY",
            }
        records["overview"]["error"] = "invalid_analysis_result"
        records["overview"]["available_at"] = job.available_at.isoformat()
        job.steps = records
    drain_pipeline(database)
    board = admin_client.get("/v1/admin/article-pipeline")
    assert board.status_code == 200, board.text
    card = next(column for column in board.json()["columns"] if column["stage"] == "analysis")[
        "items"
    ][0]
    assert card["current_analysis_step"] == "overview"
    assert card["state"] == "retry_wait"
    response = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert [step["name"] for step in detail["analysis_steps"]] == list(STEP_SPECS)
    overview = next(step for step in detail["analysis_steps"] if step["name"] == "overview")
    assert overview["attempts"] == 2 and overview["available_at"] is not None
    assert overview["error"] == "invalid_analysis_result"
    assert "DO_NOT_EXPOSE_DRAFT_OUTPUT" not in response.text + board.text
    assert "PRIVATE_CONTEXT_IDENTITY" not in response.text + board.text
    assert detail["can_publish"] is False
