"""Analysis admission and result application preserve the current article state."""

import copy
import hashlib
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import ai_content, analysis
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.models import ArticleContent, ArticleTag, ArticleTopic, Tag
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy.orm import Session

from tests.analysis.test_analysis import result
from tests.articles.test_processing_retry_eligibility_unit import scenario
from tests.support.source_readiness import configuration


@pytest.fixture
def admission(monkeypatch):
    article, facts = scenario()
    article.review_status, article.editorial_revision = "pending", 0
    for name in ("analysis", "enrichment"):
        facts[name].available_at = facts[name].created_at
    prior = facts["analysis"]
    prior.model = "unit-model"
    prior.usage = {"prompt_format": "specialist-steps-v1"}
    prior.result = result(
        topics=[
            {
                "topic_id": article.topic_links[0].topic_id,
                "role": "primary",
                "relevance": 0.9,
                "evidence": "Angular routing",
            }
        ],
    ).model_dump(mode="json")
    article.topic_links[0].origin = "ai"
    settings = configuration(ai_enabled=True, codex_model="unit-model", ai_content_not_before=None)
    monkeypatch.setattr(analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_content, "get_settings", lambda: settings)
    approved = Mock(return_value=[article.origins[0].source])
    monkeypatch.setattr(analysis, "approved_sources", approved)
    session = Mock(spec=Session)
    session.get.return_value = None
    session.scalar.side_effect = [article, None, None]
    session.scalars.return_value = []
    return SimpleNamespace(
        article=article,
        prior=prior,
        facts=facts,
        taxonomy=facts["taxonomy"],
        session=session,
        approved=approved,
        settings=settings,
    )


def request(value, **flags):
    return analysis.request_analysis(
        value.session,
        value.article.id,
        taxonomy=value.taxonomy,
        **flags,
    )


@pytest.mark.parametrize("mode", ["manual", "automatic"])
@pytest.mark.parametrize("guard", ["missing", "published", "outside-window", "rejected", "source"])
def test_admission_refuses_missing_editorially_closed_or_untrusted_articles(admission, mode, guard):
    v, automatic = admission, mode == "automatic"
    if guard == "missing":
        v.session.scalar.side_effect = [None]
    elif guard == "published":
        v.article.review_status = "approved"
        v.article.editorial_revision = 2
        assert derive_publication_readiness(v.article, **v.facts).stage == "ready"
        v.article.publication_status = "published"
        v.article.published_to_feed_at = v.facts["now"]
    elif guard == "outside-window":
        v.settings.ai_content_not_before = date(2026, 1, 1)
        v.article.published_at = v.facts["now"].replace(year=2025)
    elif guard == "rejected":
        v.article.review_status = "rejected"
    else:
        v.article.origins[0].source.approval_status = "pending"
        v.approved.return_value = []
    if automatic and guard in {"published", "outside-window"}:
        assert request(v, automatic=True) is None
    else:
        with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
            request(v, automatic=automatic)
    v.session.add.assert_not_called()


@pytest.mark.parametrize("automatic", [False, True])
def test_missing_source_text_requires_extraction_before_analysis(admission, automatic):
    v = admission
    v.article.summary = None
    if automatic:
        assert request(v, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="run articles enrich"):
            request(v)
    v.session.add.assert_not_called()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_existing_delivery_is_reused_without_replacing_its_history(admission, status):
    from tests.articles.test_processing_retry_eligibility_unit import active_job

    v = admission
    active = active_job(v.article, v.facts, "analysis", status=status)
    active.input_snapshot = analysis.source_snapshot(v.article, None)
    active.input_hash = analysis.snapshot_hash(active.input_snapshot)
    active.editorial_revision = v.article.editorial_revision
    active.prompt_version = analysis.PROMPT_VERSION
    v.session.scalar.side_effect = [v.article, active]
    before = (active.attempts, active.available_at, active.lease_token, active.lease_until)
    assert request(v) is active
    assert (active.attempts, active.available_at, active.lease_token, active.lease_until) == before
    v.session.get.assert_not_called()
    v.session.add.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_automatic_exact_input_suppression_preserves_success_or_exhausted_failure(
    admission, failed
):
    v = admission
    if failed:
        v.prior.status, v.prior.outcome = "failed", None
        v.prior.error, v.prior.result, v.prior.attempts = "codex_timeout", {}, 3
        v.article.topic_links, v.article.classification_provenance = [], {}
        v.article.ai_summary = v.article.ai_description = None
    v.session.scalars.return_value = [v.prior]
    assert request(v, automatic=True) is None
    assert v.prior.attempts == (3 if failed else 1)
    v.session.add.assert_not_called()


@pytest.mark.parametrize("forced", [False, True])
def test_explicit_request_builds_current_immutable_inputs_without_automatic_suppression(
    admission,
    forced,
):
    v = admission
    body = "Angular routing supports nested paths and guarded application navigation."
    content = ArticleContent(
        article_id=v.article.id,
        url=v.article.canonical_url,
        text=body,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
        method="body",
        retrieved_at=v.facts["now"] - timedelta(seconds=30),
    )
    v.session.get.return_value = content
    created = request(v, automatic=True, force=True) if forced else request(v)
    assert created.article_id == v.article.id
    assert created.input_snapshot == analysis.source_snapshot(v.article, content)
    assert created.input_snapshot["text"] == body
    assert created.input_hash == analysis.snapshot_hash(created.input_snapshot)
    assert created.catalog_hash == analysis.snapshot_hash(
        analysis.analysis_candidates(v.taxonomy, created.input_snapshot)
    )
    assert created.model == "unit-model" and created.prompt_version == analysis.PROMPT_VERSION
    assert created.editorial_revision == 0
    assert created.usage == {
        "prompt_format": "specialist-steps-v1",
        "requested_reason": "forced" if forced else "manual",
        "validation_version": analysis.VALIDATION_VERSION,
    }
    assert v.article.review_status == "pending" and v.article.publication_status == "unpublished"
    v.session.scalars.assert_not_called()
    v.session.add.assert_called_once_with(created)
    v.session.flush.assert_called_once()
    v.article.summary = "An editor later corrected the source summary."
    assert created.input_snapshot["source_summary"] != v.article.summary


@pytest.mark.parametrize(
    "reason",
    [
        "initial_analysis",
        "source_content_changed",
        "editorial_changed",
        "model_changed",
        "prompt_changed",
        "prompt_format_changed",
        "catalog_changed",
        "unapplied_previous_result",
    ],
)
def test_automatic_request_records_the_first_real_reason_for_reanalysis(admission, reason):
    v = admission
    latest = v.prior
    if reason == "initial_analysis":
        latest = None
    elif reason == "source_content_changed":
        v.article.summary += " Nested routes preserve parent navigation."
    elif reason == "editorial_changed":
        v.article.editorial_revision += 1
    elif reason == "model_changed":
        latest.model = "previous-model"
    elif reason == "prompt_changed":
        latest.prompt_version = "previous-prompt-contract"
    elif reason == "prompt_format_changed":
        latest.usage = {"prompt_format": "compact-evidence-v2"}
        v.session.scalars.return_value = [latest]
    elif reason == "catalog_changed":
        v.taxonomy["topics"][0]["description"] = "Angular is a web application framework."
        v.session.scalars.return_value = [latest]
    else:
        # Superseded attempts are excluded from exact-input suppression but remain
        # visible to the separate chronological latest-attempt read.
        latest.outcome = "superseded"
        latest.result = {}
        v.article.topic_links, v.article.classification_provenance = [], {}
    v.session.scalar.side_effect = [v.article, None, latest]
    created = request(v, automatic=True)
    assert created.usage["requested_reason"] == reason
    assert created.input_hash == analysis.snapshot_hash(analysis.source_snapshot(v.article, None))
    v.session.add.assert_called_once_with(created)


def test_unselected_fallback_metadata_churn_does_not_spend_another_inference(admission):
    v = admission
    fallback = {"id": str(uuid.uuid4()), "name": "Databases", "slug": "databases"}
    v.taxonomy["tags"] = [fallback]
    v.prior.catalog_snapshot = analysis.analysis_candidates(v.taxonomy, v.prior.input_snapshot)
    v.prior.catalog_hash = analysis.snapshot_hash(v.prior.catalog_snapshot)
    current = copy.deepcopy(v.taxonomy)
    current["tags"][0]["description"] = "Database architecture reference."
    v.taxonomy = current
    v.session.scalars.return_value = [v.prior]
    assert analysis.snapshot_hash(
        analysis.analysis_candidates(current, v.prior.input_snapshot)
    ) != (v.prior.catalog_hash)
    assert request(v, automatic=True) is None
    v.session.add.assert_not_called()


def test_catalog_reader_preserves_optional_metadata_and_normalizes_identifiers():
    session = Mock(spec=Session)
    topic_id, tag_id = uuid.uuid4(), uuid.uuid4()
    topic = {
        "id": topic_id,
        "name": "Angular",
        "slug": "angular",
        "kind": "framework",
        "aliases": ["AngularJS"],
        "description": "Web framework",
    }
    tag = {"id": tag_id, "name": "Guide", "slug": "guide"}
    session.execute.side_effect = [
        SimpleNamespace(mappings=lambda: [topic]),
        SimpleNamespace(mappings=lambda: [tag]),
    ]
    taxonomy = analysis.catalog(session)
    assert taxonomy == {
        "topics": [
            {
                "aliases": ["AngularJS"],
                "kind": "framework",
                "keywords": [],
                "description": "Web framework",
                "ai_description": None,
                **topic,
                "id": str(topic_id),
            }
        ],
        "tags": [
            {
                "aliases": [],
                "kind": None,
                "keywords": [],
                "description": None,
                "ai_description": None,
                **tag,
                "id": str(tag_id),
            }
        ],
    }


@pytest.fixture
def application(admission, monkeypatch):
    v = admission
    v.job = copy.deepcopy(v.prior)
    v.job.id, v.job.status, v.job.attempts = uuid.uuid4(), "queued", 0
    v.job.created_at = v.job.available_at = v.facts["now"] - timedelta(minutes=1)
    v.job.result, v.job.outcome, v.job.finished_at = {}, None, None
    start_job(v.job, v.job.created_at, 300)
    v.session.info = {}
    v.session.get.side_effect = lambda *_: None
    monkeypatch.setattr(analysis, "utcnow", lambda: v.facts["now"])
    monkeypatch.setattr(analysis, "lock_topics", Mock())
    return v


def negative_result(v):
    return analysis.AnalysisResult.model_validate(
        {
            **v.prior.result,
            "outcome": "insufficient_evidence",
            "language": None,
            "content_type": None,
            "content_format": None,
            "ai_summary": None,
            "ai_description": None,
            "topics": [{**v.prior.result["topics"][0], "role": "supporting"}],
            "reasons": ["The source supports a topic but not a complete publishable overview."],
        }
    )


def test_incomplete_analysis_can_retain_grounded_topic_matches_without_rewriting_presentation(
    application,
):
    v = application
    parsed = negative_result(v)
    v.job.result = parsed.model_dump(mode="json")
    v.session.scalars.return_value = []
    before = copy.deepcopy(
        (
            v.article.ai_summary,
            v.article.ai_description,
            v.article.language,
            v.article.content_type,
            v.article.content_format,
            v.article.classification_provenance,
            v.article.editorial_revision,
        )
    )
    analysis.apply_analysis(v.session, v.article, v.job, parsed)
    assert v.job.status == "succeeded" and v.job.outcome == "insufficient_evidence"
    links = [call.args[0] for call in v.session.add.call_args_list]
    assert len(links) == 1 and isinstance(links[0], ArticleTopic)
    assert (links[0].article_id, links[0].topic_id, links[0].origin, links[0].role) == (
        v.article.id,
        v.article.topic_links[0].topic_id,
        "ai",
        "primary",
    )
    assert (
        v.article.ai_summary,
        v.article.ai_description,
        v.article.language,
        v.article.content_type,
        v.article.content_format,
        v.article.classification_provenance,
        v.article.editorial_revision,
    ) == before
    assert v.article.publication_status == "unpublished" and v.article.review_status == "pending"
    v.session.expire.assert_called_once_with(v.article, ["topic_links", "tags"])


@pytest.mark.parametrize("retained", [False, True])
def test_valid_ready_labels_are_added_once_without_overwriting_a_retained_manual_assignment(
    application, retained
):
    v = application
    label = Tag(id=uuid.uuid4(), name="Routing", slug="routing", aliases=[])
    parsed = analysis.AnalysisResult.model_validate(
        {
            **v.prior.result,
            "tags": [{"id": str(label.id), "evidence": "routing"}],
        }
    )
    manual = ArticleTag(article_id=v.article.id, tag_id=label.id, origin="manual")
    v.session.get.side_effect = lambda model, key: (
        manual if retained and model is ArticleTag and key == (v.article.id, label.id) else None
    )
    v.session.scalars.return_value = [v.article.topic_links[0].topic_id]
    assigned = analysis.replace_classifications(v.session, v.article, parsed, origin="ai")
    saved_labels = [
        call.args[0]
        for call in v.session.add.call_args_list
        if isinstance(call.args[0], ArticleTag)
    ]
    assert assigned == {"tag_ids": [] if retained else [str(label.id)]}
    assert len(saved_labels) == int(not retained)
    if saved_labels:
        assert (saved_labels[0].article_id, saved_labels[0].tag_id, saved_labels[0].origin) == (
            v.article.id,
            label.id,
            "ai",
        )
    assert manual.origin == "manual"
    assert (v.article.language, v.article.content_type, v.article.content_format) == (
        parsed.language,
        parsed.content_type,
        parsed.content_format,
    )


@pytest.mark.parametrize("guard", ["missing", "changed-revision"])
def test_manual_classification_requires_the_current_article_and_revision(application, guard):
    v = application
    v.session.scalar.side_effect = None
    v.session.scalar.return_value = None if guard == "missing" else v.article
    body = analysis.ManualClassification.model_validate(
        {
            **{
                key: v.prior.result[key]
                for key in (
                    "developer_relevance",
                    "language",
                    "content_type",
                    "content_format",
                    "topics",
                    "tags",
                    "page_kind",
                )
            },
            "expected_revision": 1,
            "actor": "Operator",
        }
    )
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        analysis.classify_manually(v.session, v.article.id, body)
    v.session.add.assert_not_called()
    v.session.execute.assert_not_called()


@pytest.mark.parametrize("prepared_before_edit", [False, True])
def test_superseded_delivery_does_not_repeat_analysis_after_a_manual_revision_change(
    application,
    monkeypatch,
    prepared_before_edit,
):
    v = application
    v.article.editorial_revision = 1
    # Initial queued deliveries have no catalog receipt. Retries can retain a
    # prepared receipt from their previous failed attempt; neither authorizes
    # new AI work after a human edit with unchanged source text.
    v.job.catalog_hash = v.prior.catalog_hash if prepared_before_edit else None
    finish_job(v.job, "superseded", v.facts["now"])
    enqueue = Mock()
    monkeypatch.setattr(analysis, "request_analysis", enqueue)
    assert (
        analysis.refresh_superseded_analysis(v.session, v.article, v.job, taxonomy=v.taxonomy)
        is None
    )
    enqueue.assert_not_called()
    assert v.article.editorial_revision == 1 and v.article.review_status == "pending"


@pytest.mark.parametrize("preloaded_catalog", [False, True])
def test_superseded_analysis_with_unchanged_source_revision_and_catalog_does_not_respend(
    application, monkeypatch, preloaded_catalog
):
    v = application
    finish_job(v.job, "superseded", v.facts["now"])
    catalog, enqueue = Mock(return_value=v.taxonomy), Mock()
    monkeypatch.setattr(analysis, "catalog", catalog)
    monkeypatch.setattr(analysis, "request_analysis", enqueue)
    assert analysis.analysis_catalog_current(
        v.job, v.taxonomy, analysis.source_snapshot(v.article, None)
    )
    assert (
        analysis.refresh_superseded_analysis(
            v.session, v.article, v.job, **({"taxonomy": v.taxonomy} if preloaded_catalog else {})
        )
        is None
    )
    assert catalog.call_count == int(not preloaded_catalog)
    enqueue.assert_not_called()
    v.session.flush.assert_not_called()
