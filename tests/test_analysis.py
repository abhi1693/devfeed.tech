import uuid
from types import SimpleNamespace

import pytest
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleContent, ArticleTopic, utcnow
from devfeed_core.services import OperationConflict
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

TOPIC_ID = uuid.uuid4()
SNAPSHOT = {
    "url": "https://example.com/angular",
    "title": "Angular routing in JavaScript",
    "source_summary": "",
    "text": "Angular routing helps developers build navigation in JavaScript applications.",
    "text_source": "body",
}
CATALOG = {
    "topics": [{"id": str(TOPIC_ID), "name": "Angular", "slug": "angular"}],
    "tags": [],
}


def result(**values):
    return analysis.AnalysisResult.model_validate(
        {
            "outcome": "ready",
            "developer_relevance": "relevant",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_summary": "A guide to routing in Angular applications.",
            "ai_description": None,
            "topics": [
                {
                    "topic_id": str(TOPIC_ID),
                    "role": "primary",
                    "relevance": 0.9,
                    "evidence": "Angular routing",
                }
            ],
            "tags": [],
            "reasons": [],
            **values,
        }
    )


def inputs():
    article = Article(
        id=uuid.uuid4(),
        canonical_url=SNAPSHOT["url"],
        title=SNAPSHOT["title"],
        summary="",
        editorial_revision=1,
        review_status="pending",
        publication_status="unpublished",
    )
    content = ArticleContent(article_id=article.id, text=SNAPSHOT["text"], method="body")
    snapshot = analysis.source_snapshot(article, content)
    job = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        input_hash=analysis.snapshot_hash(snapshot),
        input_snapshot=snapshot,
        editorial_revision=1,
        model="configured-model",
        prompt_version=analysis.PROMPT_VERSION,
        status="running",
        attempts=1,
    )
    added, executed = [], []
    db = SimpleNamespace(
        info={},
        get=lambda model, _: content if model is ArticleContent else None,
        add=added.append,
        execute=executed.append,
        flush=lambda: None,
        expire=lambda *a: None,
    )
    return article, content, job, db, added


def test_evidence_must_be_from_input_and_ids_from_dynamic_catalog():
    analysis.validate_evidence(result(), SNAPSHOT, CATALOG)
    invalid = result()
    invalid.topics[0].evidence = "React framework"
    with pytest.raises(ValueError, match="evidence"):
        analysis.validate_evidence(invalid, SNAPSHOT, CATALOG)
    invalid = result()
    invalid.topics[0].topic_id = uuid.uuid4()
    with pytest.raises(ValueError, match="unknown"):
        analysis.validate_evidence(invalid, SNAPSHOT, CATALOG)


@pytest.mark.parametrize(
    "values",
    [
        {"outcome": "publish"},
        {"language": None},
        {"content_type": None},
        {"language": "und"},
        {"ai_summary": "a" * 1201},
        {"reasons": ["x" * 501]},
        {"title": "AI changed title"},
        {"proposed_topics": []},
        {
            "proposed_topics": [
                {
                    "name": "New topic",
                    "slug": "new-topic",
                    "kind": "technology",
                    "evidence": "Angular routing",
                }
            ]
        },
    ],
)
def test_invalid_and_out_of_scope_ai_fields_are_rejected(values):
    with pytest.raises(ValidationError):
        result(**values)


def test_duplicate_topic_and_nonfinite_relevance_are_rejected():
    topic = result().topics[0].model_dump()
    with pytest.raises(ValidationError, match="Duplicate"):
        result(topics=[topic, topic])
    with pytest.raises(ValidationError):
        result(topics=[{**topic, "relevance": float("nan")}])


def test_insufficient_evidence_can_return_unknowns_without_fabricating_prose():
    value = result(
        outcome="insufficient_evidence",
        developer_relevance="uncertain",
        language=None,
        content_type=None,
        ai_summary=None,
        topics=[],
    )
    assert value.language is None


def test_article_analysis_contract_only_selects_existing_topics():
    schema = analysis.AnalysisResult.model_json_schema()
    assert "proposed_topics" not in schema["properties"]
    assert "TopicProposal" not in schema.get("$defs", {})
    assert schema["additionalProperties"] is False
    prompt = analysis.analysis_prompt(SNAPSHOT, CATALOG)
    assert "Do not create or propose new topics" in prompt
    assert "leave it unassigned" in prompt
    analysis.validate_evidence(result(topics=[]), SNAPSHOT, {"topics": [], "tags": []})


@pytest.mark.parametrize("change", ["revision", "text", "title", "rejected"])
def test_stale_results_never_override_current_article(change):
    current, content, job, db, added = inputs()
    if change == "revision":
        current.editorial_revision += 1
    if change == "text":
        content.text = "Changed source content"
    if change == "title":
        current.title = "Changed headline"
    if change == "rejected":
        current.review_status = "rejected"
    analysis.apply_analysis(db, current, job, result())
    assert job.outcome == "superseded" and job.status == "succeeded"
    assert current.ai_summary is None and not added
    assert current.publication_status == "unpublished"


def test_analysis_applies_classification_and_ai_prose_but_never_publishes(monkeypatch):
    monkeypatch.setattr(analysis, "approved_sources", lambda *_: [uuid.uuid4()])
    current, _, job, db, added = inputs()
    current.review_status, current.publication_status = "approved", "published"
    analysis.apply_analysis(db, current, job, result())
    assert current.summary == "" and current.ai_summary == result().ai_summary
    assert current.language == "en" and current.content_type == "tutorial"
    assert current.review_status == "pending" and current.publication_status == "unpublished"
    assert current.classification_provenance["analysis_id"] == str(job.id)
    assert any(isinstance(value, ArticleTopic) for value in added)
    assert job.outcome == "applied" and job.finished_at


def test_unapproved_source_never_applies_analysis(monkeypatch):
    monkeypatch.setattr(analysis, "approved_sources", lambda *_: [])
    current, _, job, db, added = inputs()
    analysis.apply_analysis(db, current, job, result())
    assert job.outcome == "unapproved" and not added and current.ai_summary is None


def test_retry_is_bounded_and_does_not_change_editorial_state():
    _, _, job, _, _ = inputs()
    analysis.fail_analysis(job, "codex_timeout")
    assert job.status == "queued" and job.available_at > utcnow()
    assert job.lease_token is None and job.dispatched_at is None
    job.attempts = 3
    analysis.fail_analysis(job, "codex_timeout")
    assert job.status == "failed" and job.finished_at


def test_request_coalesces_and_does_not_query_network(monkeypatch):
    current, _, job, db, _ = inputs()
    monkeypatch.setattr(analysis, "approved_sources", lambda *_: [uuid.uuid4()])
    rows = iter([current, job])
    db.scalar = lambda _: next(rows)
    assert analysis.request_analysis(db, current.id) is job


def test_request_rejects_title_only_evidence(monkeypatch):
    current, _, _, db, _ = inputs()
    monkeypatch.setattr(analysis, "approved_sources", lambda *_: [uuid.uuid4()])
    rows = iter([current, None])
    db.scalar = lambda _: next(rows)
    db.get = lambda *_: None
    with pytest.raises(OperationConflict, match="Insufficient"):
        analysis.request_analysis(db, current.id)


def test_hash_excludes_generated_fields_and_changes_with_source_text():
    current, content, _, _, _ = inputs()
    digest = analysis.snapshot_hash(analysis.source_snapshot(current, content))
    current.ai_summary, current.language = "Generated prose", "ja"
    assert analysis.snapshot_hash(analysis.source_snapshot(current, content)) == digest
    content.text += " New evidence."
    assert analysis.snapshot_hash(analysis.source_snapshot(current, content)) != digest


def test_new_content_coalesced_into_active_analysis_gets_a_followup(monkeypatch):
    current, content, job, db, _ = inputs()
    content.text += " New source evidence."
    calls = []
    monkeypatch.setattr(analysis, "request_analysis", lambda *a, **kw: calls.append((a, kw)))
    analysis.finish_analysis(job, "superseded")
    analysis.refresh_superseded_analysis(db, current, job)
    assert len(calls) == 1 and calls[0][1] == {"automatic": True}


@pytest.mark.parametrize("status", ["approved", "rejected", "pending"])
def test_editorial_only_change_never_triggers_automatic_reanalysis(status, monkeypatch):
    current, _, job, db, _ = inputs()
    current.review_status = status
    analysis.finish_analysis(job, "superseded")
    monkeypatch.setattr(analysis, "request_analysis", lambda *a, **kw: pytest.fail("Requeued"))
    analysis.refresh_superseded_analysis(db, current, job)


def test_manual_correction_is_audited_preserves_prose_and_invalidates_approval(monkeypatch):
    current, _, _, db, added = inputs()
    current.ai_summary = "Existing generated prose"
    current.publication_status, current.review_status = "published", "approved"
    db.scalar = lambda _: current
    monkeypatch.setattr(analysis, "catalog", lambda _: CATALOG)
    body = analysis.ManualClassification.model_validate(
        {
            **result().model_dump(exclude={"outcome", "ai_summary", "ai_description", "reasons"}),
            "actor": "Operator",
            "expected_revision": 1,
        }
    )
    analysis.classify_manually(db, current.id, body)
    assert current.editorial_revision == 2
    assert current.review_status == "pending" and current.publication_status == "unpublished"
    assert current.ai_summary == "Existing generated prose" and current.summary == ""
    assert current.classification_provenance["origin"] == "manual"
    assert all(item.origin == "manual" for item in added if isinstance(item, ArticleTopic))
    assert any(
        isinstance(item, analysis.ArticleReview) and item.action == "classify" for item in added
    )


@pytest.mark.parametrize("force", [False, True])
def test_analysis_backfill_is_bounded_and_skips_active_or_nonpending_candidates(monkeypatch, force):
    identifier, after = uuid.uuid4(), uuid.uuid4()
    statements, requests = [], []
    db = SimpleNamespace(
        scalars=lambda statement: (
            statements.append(statement) or SimpleNamespace(all=lambda: [identifier])
        )
    )
    queued = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        analysis,
        "request_analysis",
        lambda session, article_id, **kw: requests.append((article_id, kw)) or queued,
    )
    jobs, scanned, cursor = analysis.backfill_analyses(db, 1, after=after, force=force)
    assert jobs == [queued] and scanned == 1 and cursor == str(identifier)
    assert requests == [(identifier, {"automatic": True, "force": force})]
    compiled = statements[0].compile(dialect=postgresql.dialect())
    assert "SKIP LOCKED" in str(compiled) and "NOT (EXISTS" in str(compiled)
    assert "approval_status" in str(compiled)
    assert {"pending", "unpublished", after}.issubset(
        set(value for value in compiled.params.values() if not isinstance(value, list))
    )
    with pytest.raises(ValueError):
        analysis.backfill_analyses(db, 501)
