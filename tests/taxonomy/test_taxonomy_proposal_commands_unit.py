"""Reviewed taxonomy imports preserve identity, evidence and independent approval."""

import copy
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_topic_policy, config, topic_verification
from devfeed_core import topic_proposals as proposals
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic
from devfeed_core.services import OperationConflict, RecordNotFound

from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import ACTOR, NOW, metadata_job, proposal, topic


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(
        session=Mock(), proposal=proposal(), catalog={}, settings=configuration()
    )
    value.lock, value.followup = Mock(), Mock()
    monkeypatch.setattr(proposals, "lock_topics", value.lock)
    monkeypatch.setattr(proposals, "utcnow", lambda: NOW)
    monkeypatch.setattr(proposals, "catalogs", lambda _: value.catalog)
    monkeypatch.setattr(config, "get_settings", lambda: value.settings)
    monkeypatch.setattr(proposals, "get_settings", lambda: value.settings)
    monkeypatch.setattr(article_topic_policy, "get_settings", lambda: value.settings)
    monkeypatch.setattr(proposals, "queue_relationships_after_enrichment", value.followup)

    def save(_session, draft, identifier=None):
        row = next((row for row in value.catalog.values() if row.id == identifier), None)
        if row is None:
            row = Topic(id=uuid.uuid4(), status="active")
        for key, item in draft.model_dump().items():
            setattr(row, key, item)
        return row

    value.save = Mock(side_effect=save)
    monkeypatch.setattr(proposals, "save_topic", value.save)
    return value


def review(value, decision="approved", *, draft=None, token=None):
    body = proposals.TopicReview(
        decision=decision,
        topic=draft
        if draft is not None
        else value.proposal.proposed
        if decision == "approved"
        else None,
        expected_input_hash=token or snapshot_hash(value.proposal.proposed),
        note="Reviewed the proposed identity and primary documentation.",
    )
    return proposals.review_proposal(value.session, value.proposal.id, body, ACTOR)


@pytest.mark.parametrize(
    "guard",
    ["missing", "reviewed", "changed-draft", "article-paused", "out-of-scope", "uncertain-scope"],
)
def test_review_requires_a_current_pending_allowed_and_in_scope_draft(runtime, guard):
    original = snapshot_hash(runtime.proposal.proposed)
    scope_job = None
    if guard == "reviewed":
        runtime.proposal.status, runtime.proposal.reviewed_at, runtime.proposal.reviewed_by = (
            "rejected",
            NOW,
            ACTOR,
        )
    elif guard == "changed-draft":
        runtime.proposal.proposed = {
            **runtime.proposal.proposed,
            "description": "An operator corrected the project identity.",
        }
    elif guard == "article-paused":
        runtime.proposal.origin = "article_enrichment"
        runtime.settings = configuration(article_topic_proposals_enabled=False)
    elif guard in {"out-of-scope", "uncertain-scope"}:
        scope_job = metadata_job(runtime.proposal)
        original = snapshot_hash(runtime.proposal.proposed)
        item = topic_verification.verification_input(runtime.proposal)
        check = {
            "proposal_id": item["proposal_id"],
            "input_hash": item["input_hash"],
            "verdict": "unsupported" if guard == "out-of-scope" else "uncertain",
            "relevance": {
                "verdict": "out_of_scope" if guard == "out-of-scope" else "uncertain",
                "sources": [0],
                "reason": "The independent scope review requires an operator decision.",
            },
            "fields": [
                {
                    "field": field,
                    "supported": False,
                    "sources": [],
                    "reason": "Awaiting scope resolution.",
                }
                for field in topic_verification.FIELDS
                if item["topic"].get(field)
            ],
            "aliases": [],
            "sources": [
                {
                    "url": "https://react.dev/learn",
                    "quote": "React components are JavaScript functions that return markup.",
                }
            ],
        }
        scope_job.result["topic_verification"] = topic_verification.checked_verdict(check, item)
    runtime.session.scalar.side_effect = [
        None if guard == "missing" else runtime.proposal,
        scope_job,
    ]
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        review(runtime, token=original)
    runtime.save.assert_not_called()
    runtime.followup.assert_not_called()
    runtime.session.flush.assert_not_called()


def test_approving_a_new_import_applies_the_reviewed_fields_and_records_the_applied_snapshot(
    runtime,
):
    runtime.session.scalar.side_effect = [runtime.proposal, None]
    draft = {
        **runtime.proposal.proposed,
        "description": "React is a JavaScript library for building user interfaces.",
    }
    before = copy.deepcopy(runtime.proposal.proposed)
    result = review(runtime, draft=draft)
    row = runtime.catalog["react"]
    assert result is runtime.proposal and result.status == "approved"
    assert row.status == "active" and row.description == draft["description"]
    assert result.topic_id == row.id and result.applied == proposals.snapshot(row)
    assert result.proposed == before
    assert result.reviewed_at == NOW and result.reviewed_by == ACTOR
    assert result.review_note == "Reviewed the proposed identity and primary documentation."
    assert runtime.save.call_args.args[2] is None
    runtime.followup.assert_called_once_with(runtime.session, result)


def test_update_approval_is_bound_to_the_original_topic_snapshot_and_keeps_its_identity(runtime):
    current = topic()
    runtime.catalog[current.slug] = current
    runtime.proposal = proposal(current)
    runtime.session.get.return_value = current
    runtime.session.scalar.side_effect = [runtime.proposal, None]
    draft = {**runtime.proposal.proposed, "keywords": ["react-components"]}
    review(runtime, draft=draft)
    assert runtime.save.call_args.args[2] == current.id
    assert current.keywords == ["react-components"]
    assert (
        runtime.proposal.topic_id == current.id
        and runtime.proposal.applied == proposals.snapshot(current)
    )


@pytest.mark.parametrize("changed", ["missing-update", "changed-update", "duplicate-slug"])
def test_review_cannot_overwrite_a_later_topic_edit_or_an_existing_identity(runtime, changed):
    if changed == "duplicate-slug":
        runtime.catalog["react"] = topic()
    else:
        current = topic()
        runtime.catalog["react"] = current
        runtime.proposal = proposal(current)
        if changed == "changed-update":
            current.description = "A newer human description."
        runtime.session.get.return_value = None if changed == "missing-update" else current
    runtime.session.scalar.side_effect = [runtime.proposal, None]
    with pytest.raises(
        OperationConflict,
        match="Topic changed" if changed != "duplicate-slug" else "slug already exists",
    ):
        review(runtime)
    runtime.save.assert_not_called()
    runtime.followup.assert_not_called()


@pytest.mark.parametrize("current_status", [None, "proposed", "active"])
def test_rejection_closes_only_its_proposed_placeholder_and_never_changes_active_topics(
    runtime, current_status
):
    current = topic(status=current_status) if current_status else None
    runtime.proposal.topic_id = current.id if current else None
    runtime.session.get.return_value = current
    runtime.session.scalar.return_value = runtime.proposal
    review(runtime, decision="rejected")
    assert runtime.proposal.status == "rejected" and runtime.proposal.reviewed_by == ACTOR
    if current:
        assert current.status == ("rejected" if current_status == "proposed" else "active")
    runtime.save.assert_not_called()
    runtime.followup.assert_not_called()


def import_preview(value, rows):
    body = proposals.TopicImport(
        format="json", content=json.dumps(rows), source_name="Operator taxonomy"
    )
    value.session.scalars.return_value = []  # Already-scoped pending slugs.
    preview = proposals.preview_import(value.session, body)
    return proposals.TopicImportSubmit(**body.model_dump(), preview_token=preview.preview_token)


@pytest.mark.parametrize("research", [False, True])
def test_import_submission_groups_changes_and_retains_unchanged_rows_without_approving(
    runtime, research
):
    runtime.settings = configuration(ai_enabled=research, auto_research_imports=True)
    current, retained = topic(), topic("Python")
    runtime.catalog.update({current.slug: current, retained.slug: retained})
    body = import_preview(
        runtime,
        [
            {
                **proposals.topic_draft(current).model_dump(mode="json"),
                "description": "Reviewed new React description.",
            },
            proposals.topic_draft(retained).model_dump(mode="json"),
            {"name": "JavaScript", "slug": "javascript", "kind": "language"},
        ],
    )
    result = proposals.submit_import(runtime.session, body, ACTOR)
    assert len(result) == 2 and {row.action for row in result} == {"create", "update"}
    assert len({row.batch_id for row in result}) == 1
    update = next(row for row in result if row.action == "update")
    assert update.topic_id == current.id and update.baseline == proposals.snapshot(current)
    created = next(row for row in result if row.action == "create")
    assert created.topic_id is None and created.baseline is None
    assert all(
        row.created_by == ACTOR and row.origin == "import" and row.research_requested is research
        for row in result
    )
    assert all(
        row.evidence[0]["content_hash"] == proposals.fingerprint(body.content) for row in result
    )
    runtime.save.assert_not_called()
    runtime.followup.assert_not_called()
    assert current.description is None


@pytest.mark.parametrize("changed", ["draft", "catalog", "pending-slug"])
def test_import_submission_rechecks_the_preview_against_current_evidence(runtime, changed):
    body = import_preview(runtime, [{"name": "React", "slug": "react", "kind": "technology"}])
    if changed == "draft":
        body.content = json.dumps([{"name": "Vue", "slug": "vue", "kind": "technology"}])
    elif changed == "catalog":
        runtime.catalog["python"] = topic("Python")
    else:
        runtime.session.scalars.return_value = ["react"]
    with pytest.raises(OperationConflict, match="changed"):
        proposals.submit_import(runtime.session, body, ACTOR)
    runtime.session.add.assert_not_called()


def test_import_with_only_unchanged_rows_cannot_create_empty_work(runtime):
    current = topic()
    runtime.catalog[current.slug] = current
    body = import_preview(runtime, [proposals.topic_draft(current).model_dump(mode="json")])
    with pytest.raises(OperationConflict, match="Resolve all import issues"):
        proposals.submit_import(runtime.session, body, ACTOR)
    runtime.session.add.assert_not_called()


def enrichment_preview(value):
    current = topic(keywords=["existing-keyword"])
    value.catalog[current.slug] = current
    body = proposals.TopicEnrichmentPreview(
        topic=proposals.topic_draft(current),
        preview_token=snapshot_hash(
            {"topic": proposals.snapshot(current), "evidence": "Scoped article tag evidence"}
        ),
        articles_examined=2,
        suggestions=[
            proposals.KeywordSuggestion(
                keyword="hooks",
                article_count=2,
                articles=[
                    {
                        "id": str(uuid.uuid4()),
                        "title": f"React hooks guide {index}",
                        "url": f"https://react.dev/hooks-{index}",
                    }
                    for index in range(2)
                ],
            )
        ],
    )
    return current, body


def test_enrichment_proposes_only_selected_supported_keywords_and_preserves_the_baseline(
    runtime, monkeypatch
):
    runtime.settings = configuration(article_topic_proposals_enabled=True)
    current, preview = enrichment_preview(runtime)
    read = Mock(return_value=preview)
    monkeypatch.setattr(proposals, "preview_enrichment", read)
    runtime.session.scalar.return_value = None
    body = proposals.TopicEnrichmentSubmit(preview_token=preview.preview_token, keywords=["hooks"])
    result = proposals.submit_enrichment(runtime.session, current.id, body, ACTOR)
    assert result.proposed["keywords"] == ["existing-keyword", "hooks"]
    assert result.baseline == proposals.snapshot(current)
    assert result.evidence == [preview.suggestions[0].model_dump()]
    assert result.origin == "article_enrichment" and result.created_by == ACTOR
    assert current.keywords == ["existing-keyword"] and current.status == "active"
    runtime.save.assert_not_called()


@pytest.mark.parametrize("guard", ["paused", "stale", "unsupported", "existing-proposal"])
def test_keyword_enrichment_requires_enabled_current_preview_and_no_pending_review(
    runtime, monkeypatch, guard
):
    runtime.settings = configuration(article_topic_proposals_enabled=guard != "paused")
    current, preview = enrichment_preview(runtime)
    read = Mock(return_value=preview)
    monkeypatch.setattr(proposals, "preview_enrichment", read)
    runtime.session.scalar.return_value = uuid.uuid4() if guard == "existing-proposal" else None
    token = snapshot_hash({"older": "evidence"}) if guard == "stale" else preview.preview_token
    body = proposals.TopicEnrichmentSubmit(
        preview_token=token, keywords=["unverified" if guard == "unsupported" else "hooks"]
    )
    with pytest.raises(OperationConflict):
        proposals.submit_enrichment(runtime.session, current.id, body, ACTOR)
    runtime.session.add.assert_not_called()
    if guard == "paused":
        read.assert_not_called()
