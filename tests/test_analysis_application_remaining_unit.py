"""Analysis application keeps incomplete evidence and manual decisions separate.

Repository rows and existing assignment identities are supplied. Assertions cover
Python policy and requested writes, not SQL deletion/filter or locking semantics.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import analysis
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.models import ArticleTag, ArticleTopic, Tag
from devfeed_core.services import OperationConflict, RecordNotFound
from test_analysis_admission_contracts_unit import admission as admission


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
