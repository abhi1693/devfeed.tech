"""Publication authority, freshness, deterministic rejection, and decision idempotency."""

import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import publication_policy as policy
from devfeed_core.analysis import snapshot_hash, source_snapshot
from devfeed_core.models import ArticlePublicationDecision

ID, JOB = uuid.UUID(int=1), uuid.UUID(int=2)


@pytest.fixture
def scenario(monkeypatch):
    settings = NS(full_automation=False)
    monkeypatch.setattr(policy, "get_settings", lambda: settings)
    monkeypatch.setattr(policy, "analysis_catalog_current", lambda *_: True)
    monkeypatch.setattr(policy, "catalog", lambda _: {"topics": [], "tags": []})
    article = NS(
        id=ID,
        canonical_url="https://example.com/article",
        title="Python typing",
        summary="Useful engineering evidence",
        ai_summary=None,
        metadata_source_type="feed",
        content_type="tutorial",
        content_format="article",
        language="en",
        editorial_revision=4,
        review_status="pending",
        publication_status="unpublished",
        classification_provenance={
            "analysis_id": str(JOB),
            "developer_relevance": "relevant",
            "page_kind": "article",
        },
        topic_links=[NS(role="primary", topic=NS(status="active"))],
        origins=[
            NS(
                source=NS(
                    id=ID,
                    approval_status="approved",
                    enabled=True,
                    publication_policy="auto",
                    publication_policy_revision=3,
                )
            )
        ],
    )
    job = NS(
        id=JOB,
        status="succeeded",
        outcome="applied",
        editorial_revision=4,
        input_hash=snapshot_hash(source_snapshot(article, None)),
        result={"outcome": "ready", "developer_relevance": "relevant", "page_kind": "article"},
    )
    session = Mock()
    session.get.return_value = None
    session.scalar.return_value = None
    return settings, article, job, session


def test_current_ready_analysis_and_approved_auto_source_are_publishable(scenario):
    _, article, job, session = scenario
    value = policy.evaluate_publication(session, article, job, taxonomy={})
    assert value["status"] == "would_publish" and value["reasons"] == []
    assert value["mode"] == "auto" and value["source_policy_revision"] == 3
    assert value["analysis_id"] == str(JOB) and value["policy_version"] == policy.POLICY_VERSION


@pytest.mark.parametrize("change", ["disabled", "unapproved", "manual", "absent"])
def test_ineligible_sources_cannot_authorize_publication(scenario, change):
    _, article, job, session = scenario
    source = article.origins[0].source
    if change == "absent":
        article.origins = []
    else:
        setattr(
            source,
            {
                "disabled": "enabled",
                "unapproved": "approval_status",
                "manual": "publication_policy",
            }[change],
            {"disabled": False, "unapproved": "pending", "manual": "manual"}[change],
        )
    value = policy.evaluate_publication(session, article, job)
    assert value["mode"] == "manual" and value["source_id"] is None
    assert "source_policy_manual" in value["reasons"]


def test_auto_source_wins_over_preview_in_a_stable_order(scenario):
    _, article, job, session = scenario
    preview = NS(
        **{
            **vars(article.origins[0].source),
            "id": uuid.UUID(int=0),
            "publication_policy": "preview",
        }
    )
    article.origins.insert(0, NS(source=preview))
    assert policy.evaluate_publication(session, article, job)["source_id"] == str(ID)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "current_analysis_required"),
        ("failed", "current_analysis_required"),
        ("outcome", "current_analysis_required"),
        ("hash", "current_analysis_required"),
        ("revision", "current_analysis_required"),
        ("provenance", "current_analysis_required"),
        ("evidence", "insufficient_analysis_evidence"),
        ("result", "analysis_uncertain"),
        ("relevance", "analysis_uncertain"),
        ("page", "analysis_uncertain"),
        ("reviewed", "editorial_decision_exists"),
        ("published", "editorial_decision_exists"),
    ],
)
def test_stale_uncertain_and_previously_reviewed_articles_are_blocked(scenario, change, reason):
    _, article, job, session = scenario
    if change == "missing":
        job = None
    elif change == "provenance":
        article.classification_provenance["analysis_id"] = "old"
    elif change == "reviewed":
        article.review_status = "approved"
    elif change == "published":
        article.publication_status = "published"
    elif change in {"result", "relevance", "page"}:
        job.result[
            {"result": "outcome", "relevance": "developer_relevance", "page": "page_kind"}[change]
        ] = "unknown"
    else:
        field, value = {
            "failed": ("status", "failed"),
            "outcome": ("outcome", "cancelled"),
            "hash": ("input_hash", "stale"),
            "revision": ("editorial_revision", 2),
            "evidence": ("outcome", "insufficient_evidence"),
        }[change]
        setattr(job, field, value)
    assert reason in policy.evaluate_publication(session, article, job)["reasons"]


def test_empty_source_text_and_changed_catalog_block_automation(scenario, monkeypatch):
    _, article, job, session = scenario
    article.summary = "12345"
    monkeypatch.setattr(policy, "analysis_catalog_current", lambda *_: False)
    reasons = policy.evaluate_publication(session, article, job)["reasons"]
    assert {"insufficient_source_text", "current_catalog_required"} <= set(reasons)


def test_extracted_text_can_supply_evidence_when_feed_summary_is_empty(scenario):
    _, article, job, session = scenario
    article.summary = ""
    content = NS(text="Extracted engineering evidence", method="readability")
    session.get.return_value = content
    job.input_hash = snapshot_hash(source_snapshot(article, content))
    assert (
        "insufficient_source_text"
        not in policy.evaluate_publication(session, article, job)["reasons"]
    )


@pytest.mark.parametrize("full", [False, True])
def test_manual_review_is_respected_unless_full_automation_is_explicit(scenario, full):
    settings, article, job, session = scenario
    settings.full_automation = full
    session.scalar.side_effect = [None] if full else [None, ID]
    value = policy.evaluate_publication(session, article, job)
    assert ("human_review_required" in value["reasons"]) is not full
    assert value["full_automation"] is full


@pytest.mark.parametrize(
    "mode,status,actions",
    [("auto", "published", ["approve", "publish"]), ("preview", "would_publish", [])],
)
def test_preview_records_decision_while_auto_approves_before_publishing(
    scenario, monkeypatch, mode, status, actions
):
    _, article, job, session = scenario
    article.origins[0].source.publication_policy = mode
    decide = Mock()
    monkeypatch.setattr(policy, "decide_article", decide)
    value = policy.apply_publication_policy(session, article, job)
    assert value["status"] == status
    assert [call.args[2].action for call in decide.call_args_list] == actions
    assert all(call.args[2].expected_revision == 4 for call in decide.call_args_list)
    saved = session.add.call_args.args[0]
    assert isinstance(saved, ArticlePublicationDecision) and saved.decision == value
    assert job.result["publication_policy"] == value


def test_existing_fingerprint_reuses_decision_without_repeating_editorial_actions(
    scenario, monkeypatch
):
    _, article, job, session = scenario
    previous = {"status": "published"}
    session.scalar.side_effect = [None, None, NS(decision=previous)]
    decide = Mock()
    monkeypatch.setattr(policy, "decide_article", decide)
    assert policy.apply_publication_policy(session, article, job) is previous
    decide.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("paywalled", [True, False, None])
def test_only_explicit_successful_paywall_evidence_rejects_even_without_analysis(
    scenario, monkeypatch, paywalled
):
    _, article, _, session = scenario
    session.scalar.side_effect = [
        NS(
            id=JOB,
            status="succeeded",
            result={"paywalled": paywalled, "paywall_reason": "subscriber"},
        ),
        None,
        None,
    ]
    decide = Mock()
    monkeypatch.setattr(policy, "decide_article", decide)
    value = policy.apply_publication_policy(session, article, None)
    assert value["status"] == ("rejected" if paywalled is True else "blocked")
    assert decide.call_count == int(paywalled is True)
    if paywalled is True:
        assert value["reasons"] == ["paywalled_content"]
        assert "paywall" in decide.call_args.args[2].note


@pytest.mark.parametrize("negative", ["unrelated", "non_article", "both"])
def test_full_automation_rejects_current_explicit_negative_classification(
    scenario, monkeypatch, negative
):
    settings, article, job, session = scenario
    settings.full_automation = True
    if negative in {"unrelated", "both"}:
        job.result["developer_relevance"] = "unrelated"
    if negative in {"non_article", "both"}:
        job.result["page_kind"] = "non_article"
    session.scalar.side_effect = [None, JOB, None]
    decide = Mock()
    monkeypatch.setattr(policy, "decide_article", decide)
    value = policy.apply_publication_policy(session, article, job)
    assert value["status"] == "rejected"
    assert value["reasons"] == (
        ["unrelated_content", "non_article_content"]
        if negative == "both"
        else ["unrelated_content" if negative == "unrelated" else "non_article_content"]
    )
    assert decide.call_args.args[2].action == "reject"


@pytest.mark.parametrize(
    "guard", ["stale", "superseded", "empty", "reviewed", "published", "manual", "catalog"]
)
def test_negative_results_cannot_override_freshness_or_existing_decisions(
    scenario, monkeypatch, guard
):
    settings, article, job, session = scenario
    settings.full_automation = True
    job.result["developer_relevance"] = "unrelated"
    if guard == "stale":
        job.input_hash = "old"
    elif guard == "empty":
        article.summary = "123"
        job.input_hash = snapshot_hash(source_snapshot(article, None))
    elif guard in {"reviewed", "published"}:
        setattr(
            article,
            "review_status" if guard == "reviewed" else "publication_status",
            "approved" if guard == "reviewed" else "published",
        )
    elif guard == "manual":
        article.origins = []
    elif guard == "catalog":
        monkeypatch.setattr(policy, "analysis_catalog_current", lambda *_: False)
    session.scalar.side_effect = (
        [None, None] if guard == "stale" else [None, ID if guard == "superseded" else JOB, None]
    )
    decide = Mock()
    monkeypatch.setattr(policy, "decide_article", decide)
    assert policy.apply_publication_policy(session, article, job)["status"] == "blocked"
    decide.assert_not_called()
