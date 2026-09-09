"""Topic deletion preserves articles and reconciles their links transactionally."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_aggregator import topic_analysis_tasks
from devfeed_core.analysis import AnalysisResult, apply_analysis
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.services import OperationConflict
from devfeed_core.topic_deletion import delete_topic
from devfeed_core.topic_relationships import topic_snapshot
from devfeed_core.topics import lock_topics
from devfeed_core.urls import fingerprint
from sqlalchemy import event, func, select

pytestmark = pytest.mark.integration


def pending_replacement(database, **values):
    from devfeed_core.topic_proposals import TopicDraft

    draft = TopicDraft(
        name="Vercel", slug="vercel", kind="platform", aliases=["Original"], **values
    )
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug=draft.slug,
            action="create",
            origin="import",
            source_name="GitHub",
            proposed=draft.model_dump(mode="json"),
            evidence=[],
            created_by={"subject": "admin"},
        )
        session.add(proposal)
        session.flush()
        return proposal.id, draft.model_dump(mode="json")


@pytest.fixture
def linked_topic(database):
    with database.begin() as session:
        topics = [
            Topic(name=name, slug=name.lower(), kind="technology", status=status)
            for name, status in [
                ("Original", "active"),
                ("Replacement", "active"),
                ("Other", "active"),
                ("Inactive", "proposed"),
            ]
        ]
        session.add_all(topics)
        articles = [
            Article(
                title=f"Article {i}",
                canonical_url=f"https://example.com/{i}",
                url_hash=fingerprint(f"https://example.com/{i}"),
                summary="Original article summary remains intact.",
                publication_status="published" if i in [0, 3] else "unpublished",
                review_status="rejected" if i == 1 else "approved",
                editorial_revision=4,
                classification_provenance={"developer_relevance": "relevant"},
            )
            for i in range(4)
        ]
        session.add_all(articles)
        session.flush()
        old, replacement, other, _ = topics
        for article, topic, role, origin in [
            (articles[0], old, "primary", "ai"),
            (articles[0], replacement, "comparison", "ai"),
            (articles[0], other, "supporting", "manual"),
            (articles[1], old, "supporting", "manual"),
            (articles[1], replacement, "primary", "manual"),
            (articles[2], old, "incidental", "ai"),
            (articles[3], replacement, "primary", "manual"),
        ]:
            session.add(
                ArticleTopic(
                    article_id=article.id,
                    topic_id=topic.id,
                    role=role,
                    origin=origin,
                    relevance=0.9,
                    evidence=f"Evidence for {topic.name}",
                )
            )
        tag = Tag(name="Attached tag", slug="attached-tag", topic_id=old.id)
        session.add(tag)
        pending = TopicProposal(
            batch_id=uuid.uuid4(),
            topic_id=old.id,
            slug=old.slug,
            action="update",
            origin="import",
            source_name="Import",
            proposed={"name": old.name},
            baseline={},
            evidence=[],
            created_by={"subject": "admin"},
        )
        history = TopicProposal(
            batch_id=uuid.uuid4(),
            topic_id=old.id,
            slug=old.slug,
            action="create",
            origin="import",
            source_name="Import",
            proposed={"name": old.name},
            applied={},
            status="approved",
            reviewed_at=utcnow(),
            reviewed_by={"subject": "admin"},
            evidence=[],
            created_by={"subject": "admin"},
        )
        session.add_all([pending, history])
        session.flush()
        session.add(ArticleTag(article_id=articles[0].id, tag_id=tag.id, origin="manual"))
        jobs = [
            TopicAnalysisJob(
                topic_id=t.id,
                input_hash="a" * 64,
                input_snapshot={},
                requested_by={"subject": "admin"},
                prompt_version="test",
                status="running",
                lease_token=uuid.uuid4(),
            )
            for t in [old, other]
        ]
        metadata = TopicAnalysisJob(
            proposal_id=pending.id,
            input_hash="b" * 64,
            input_snapshot={},
            requested_by={"subject": "admin"},
            prompt_version="test",
        )
        session.add_all([*jobs, metadata])
        session.flush()
        for i, (source, target) in enumerate([(old, other), (other, old)]):
            session.add(
                TopicRelation(topic_id=source.id, related_topic_id=target.id, relation="related_to")
            )
            session.add(
                TopicRelationProposal(
                    job_id=jobs[i].id,
                    topic_id=source.id,
                    related_topic_id=target.id,
                    relation="depends_on",
                    explanation="Direct dependency",
                    evidence_url="https://example.com/docs",
                    evidence_title="Documentation",
                    evidence_quote="This is a direct dependency.",
                    topic_snapshot=topic_snapshot(source),
                    related_topic_snapshot=topic_snapshot(target),
                    created_by={"subject": "admin"},
                )
            )
        session.add(
            TopicRelation(topic_id=replacement.id, related_topic_id=other.id, relation="related_to")
        )
        return dict(
            topics=[t.id for t in topics],
            articles=[a.id for a in articles],
            tag=tag.id,
            proposals=[pending.id, history.id],
            jobs=[j.id for j in jobs],
            metadata=metadata.id,
        )


@pytest.mark.parametrize("relink", [False, True])
def test_delete_unpublishes_reconciles_links_and_preserves_history(
    admin_client, database, linked_topic, monkeypatch, relink
):
    data = linked_topic
    old, replacement, other, _ = data["topics"]
    path = f"/v1/admin/topics/{old}"
    preview = admin_client.get(path + "/delete-preview")
    assert preview.status_code == 200
    assert preview.json() == dict(
        articles=3,
        published_articles=1,
        tags=1,
        relationships=2,
        relationship_proposals=2,
        research_jobs=1,
        pending_topic_proposals=1,
    )
    params = {"replacement_topic_id": str(replacement)} if relink else {}
    response = admin_client.delete(path, params=params)
    assert response.status_code == 204, response.text
    with database() as session:
        assert session.get(Topic, old) is None
        for i, identifier in enumerate(data["articles"]):
            article = session.get(Article, identifier)
            assert (
                article.title == f"Article {i}"
                and article.summary == "Original article summary remains intact."
            )
            if i == 3:
                assert article.publication_status == "published" and article.editorial_revision == 4
                continue
            assert article.publication_status == "unpublished" and article.editorial_revision == 5
            assert article.review_status == ("rejected" if i == 1 else "pending")
            assert article.classification_provenance["developer_relevance"] == "relevant"
            assert article.classification_provenance["topic_change"]["replacement_topic_id"] == (
                str(replacement) if relink else None
            )
            assert session.get(ArticleTopic, (identifier, old)) is None
            audit = session.scalar(
                select(ArticleReview).where(ArticleReview.article_id == identifier)
            )
            assert audit.actor == "integration-admin" and audit.revision == 5
            assert audit.action == ("topic_relink" if relink else "topic_delete")
        assert session.get(ArticleTopic, (data["articles"][0], other)).origin == "manual"
        if relink:
            for identifier, role, evidence in [
                (data["articles"][0], "primary", "Evidence for Original"),
                (data["articles"][1], "primary", "Evidence for Replacement"),
                (data["articles"][2], "incidental", "Evidence for Original"),
            ]:
                link = session.get(ArticleTopic, (identifier, replacement))
                assert (link.role, link.origin, link.evidence) == (role, "manual", evidence)
        else:
            assert (
                session.get(ArticleTopic, (data["articles"][0], replacement)).role == "comparison"
            )
            assert session.get(ArticleTopic, (data["articles"][2], replacement)) is None
        assert session.get(Tag, data["tag"]).topic_id == (replacement if relink else None)
        assert session.get(ArticleTag, (data["articles"][0], data["tag"])) is not None
        assert session.scalar(select(func.count()).select_from(TopicRelation)) == 1
        assert session.scalar(select(func.count()).select_from(TopicRelationProposal)) == 0
        assert session.get(TopicAnalysisJob, data["jobs"][0]) is None
        assert session.get(TopicAnalysisJob, data["jobs"][1]) is not None
        pending = session.get(TopicProposal, data["proposals"][0])
        assert pending.status == "rejected" and pending.topic_id is None
        assert pending.reviewed_by["subject"] == "integration-admin"
        historical = session.get(TopicProposal, data["proposals"][1])
        assert historical.status == "approved" and historical.topic_id is None
    # Queued deliveries of a removed research job are harmless, and metadata
    # research cannot resurrect the now-rejected topic update proposal.
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    topic_analysis_tasks._analyze(data["jobs"][0])
    topic_analysis_tasks._analyze(data["metadata"])
    with database() as session:
        assert session.get(TopicAnalysisJob, data["metadata"]).outcome == "superseded"
    assert admin_client.get(path + "/delete-preview").status_code == 404


@pytest.mark.parametrize("replacement", ["self", "missing"])
def test_invalid_replacement_leaves_topic_and_publication_unchanged(
    admin_client, database, linked_topic, replacement
):
    old = linked_topic["topics"][0]
    target = old if replacement == "self" else uuid.uuid4()
    response = admin_client.delete(
        f"/v1/admin/topics/{old}", params={"replacement_topic_id": str(target)}
    )
    assert response.status_code == (404 if replacement == "missing" else 409)
    with database() as session:
        assert session.get(Topic, old) is not None
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "published"
        assert session.scalar(select(func.count()).select_from(ArticleReview)) == 0


@pytest.mark.parametrize("model,key", [(Article, "articles"), (TopicAnalysisJob, "jobs")])
def test_worker_lock_contention_rolls_back_without_deadlocking(database, linked_topic, model, key):
    old = linked_topic["topics"][0]

    def deleting():
        with database.begin() as session, pytest.raises(OperationConflict, match="being updated"):
            delete_topic(session, old, {"subject": "admin"})

    with database.begin() as worker:
        worker.scalar(select(model).where(model.id == linked_topic[key][0]).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(deleting).result(timeout=5)
        # The deletion released its topic lock, so the worker can finish normally.
        lock_topics(worker)
    with database() as session:
        assert session.get(Topic, old) is not None
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "published"
    with database.begin() as session:
        delete_topic(session, old, {"subject": "admin"})


def test_late_failure_rolls_back_every_unpublish_and_link_change(database, linked_topic):
    old, replacement = linked_topic["topics"][:2]
    with database.begin() as session:
        engine = session.get_bind()

        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO article_reviews "):
                raise RuntimeError("Injected final-write failure")

        event.listen(engine, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="Injected"):
                delete_topic(session, old, {"subject": "admin"}, replacement)
        finally:
            event.remove(engine, "before_cursor_execute", fail)
    with database() as session:
        assert session.get(Topic, old) is not None
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "published"
        assert session.get(Tag, linked_topic["tag"]).topic_id == old
        assert session.scalar(select(func.count()).select_from(ArticleReview)) == 0
        assert session.scalar(select(func.count()).select_from(TopicRelationProposal)) == 2


def test_inflight_article_analysis_cannot_undo_topic_deletion(database, linked_topic):
    identifier = linked_topic["articles"][0]
    with database.begin() as session:
        job = ArticleAnalysisJob(
            article_id=identifier,
            editorial_revision=4,
            input_hash="a" * 64,
            input_snapshot={},
            status="running",
            prompt_version="test",
        )
        session.add(job)
        session.flush()
        job_id = job.id
    with database.begin() as session:
        delete_topic(
            session, linked_topic["topics"][0], {"subject": "admin"}, linked_topic["topics"][1]
        )
    result = AnalysisResult(
        developer_relevance="uncertain",
        language=None,
        content_type=None,
        content_format=None,
        topics=[],
        tags=[],
        outcome="insufficient_evidence",
        ai_summary=None,
        ai_description=None,
        reasons=[],
    )
    with database.begin() as session:
        article = session.get(Article, identifier)
        job = session.get(ArticleAnalysisJob, job_id)
        apply_analysis(session, article, job, result)
        assert job.outcome == "superseded" and article.publication_status == "unpublished"
        assert session.get(ArticleTopic, (identifier, linked_topic["topics"][1])).origin == "manual"


@pytest.mark.parametrize("status", ["proposed", "rejected"])
def test_replacement_topics_can_be_inactive(admin_client, database, linked_topic, status):
    old, _, _, replacement = linked_topic["topics"]
    with database.begin() as session:
        session.get(Topic, replacement).status = status
    response = admin_client.delete(
        f"/v1/admin/topics/{old}", params={"replacement_topic_id": str(replacement)}
    )
    assert response.status_code == 204, response.text
    with database() as session:
        assert session.get(Topic, replacement).status == status
        assert (
            session.get(ArticleTopic, (linked_topic["articles"][0], replacement)).origin == "manual"
        )
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "unpublished"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_pending_replacement_receives_links_but_still_needs_review(
    admin_client, database, linked_topic, decision
):
    old = linked_topic["topics"][0]
    proposal_id, draft = pending_replacement(database)
    response = admin_client.delete(
        f"/v1/admin/topics/{old}", params={"replacement_proposal_id": str(proposal_id)}
    )
    assert response.status_code == 204, response.text
    with database() as session:
        proposal = session.get(TopicProposal, proposal_id)
        replacement_id = proposal.topic_id
        assert proposal.status == "pending" and proposal.reviewed_at is None
        replacement = session.get(Topic, replacement_id)
        assert replacement.status == "proposed" and replacement.aliases == ["Original"]
        assert proposal.baseline["status"] == "proposed"
        assert session.get(Topic, old) is None
        for article_id in linked_topic["articles"][:3]:
            assert session.get(ArticleTopic, (article_id, replacement_id)).origin == "manual"
            assert session.get(Article, article_id).publication_status == "unpublished"
        assert session.get(Tag, linked_topic["tag"]).topic_id == replacement_id
    response = admin_client.post(
        f"/v1/admin/topic-proposals/{proposal_id}/review",
        json={"decision": decision, "topic": draft if decision == "approved" else None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["topic_id"] == str(replacement_id)
    with database() as session:
        assert session.get(Topic, replacement_id).status == (
            "active" if decision == "approved" else "rejected"
        )
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "unpublished"


def test_pending_replacement_identity_conflict_rolls_back_cleanup(
    admin_client, database, linked_topic
):
    old = linked_topic["topics"][0]
    proposal_id, _ = pending_replacement(database)
    with database.begin() as session:
        proposal = session.get(TopicProposal, proposal_id)
        proposal.proposed = {**proposal.proposed, "name": "Other"}
    response = admin_client.delete(
        f"/v1/admin/topics/{old}", params={"replacement_proposal_id": str(proposal_id)}
    )
    assert response.status_code == 409, response.text
    with database() as session:
        assert session.get(Topic, old) is not None
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "published"
        assert session.get(Tag, linked_topic["tag"]).topic_id == old
        assert session.get(TopicProposal, proposal_id).topic_id is None
        assert session.scalar(select(func.count()).select_from(TopicRelationProposal)) == 2


def test_replacement_picker_includes_pending_and_all_topic_statuses(
    admin_client, database, linked_topic
):
    old, _, other, _ = linked_topic["topics"]
    proposal_id, _ = pending_replacement(database)
    with database.begin() as session:
        session.get(Topic, other).status = "rejected"
    path = "/v1/admin/topic-replacements"
    response = admin_client.get(path, params={"exclude_topic_id": str(old)})
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert response.json()["total"] == 4
    assert {row["status"] for row in rows} == {"active", "proposed", "rejected", "pending"}
    assert str(old) not in {row["id"] for row in rows}
    pending = next(row for row in rows if row["status"] == "pending")
    assert pending["id"] == f"proposal:{proposal_id}" and pending["proposal_id"] == str(proposal_id)
    assert pending["name"] == "Vercel"
    filtered = admin_client.get(path, params={"q": "vercel", "limit": 1}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == pending["id"]
    page = admin_client.get(
        path, params={"exclude_topic_id": str(old), "offset": 2, "limit": 2}
    ).json()
    assert page["total"] == 4 and page["items"] == rows[2:]
    assert admin_client.get(path, params={"limit": 101}).status_code == 422


@pytest.mark.parametrize("state", ["approved", "rejected", "missing", "orphan-update", "both"])
def test_invalid_pending_replacement_preserves_source(admin_client, database, linked_topic, state):
    old, replacement = linked_topic["topics"][:2]
    proposal_id, _ = pending_replacement(database)
    with database.begin() as session:
        proposal = session.get(TopicProposal, proposal_id)
        if state in {"approved", "rejected"}:
            proposal.status = state
            proposal.reviewed_at = utcnow()
            proposal.reviewed_by = {"subject": "admin"}
            if state == "approved":
                proposal.applied = {"draft": proposal.proposed}
        elif state == "orphan-update":
            proposal.action = "update"
    params = {"replacement_proposal_id": str(uuid.uuid4() if state == "missing" else proposal_id)}
    if state == "both":
        params["replacement_topic_id"] = str(replacement)
    response = admin_client.delete(f"/v1/admin/topics/{old}", params=params)
    assert response.status_code == (404 if state == "missing" else 409), response.text
    with database() as session:
        assert session.get(Topic, old) is not None
        assert session.get(Article, linked_topic["articles"][0]).publication_status == "published"
