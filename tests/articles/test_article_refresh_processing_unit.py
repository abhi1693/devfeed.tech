"""Private preparation preserves approved inputs and exact artifact authority.

Repository lookups supply explicit rows here; SQL locking, transactional publication,
and worker interleavings remain covered by the native maintenance suites.
"""

import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import analysis, article_jobs, article_refresh
from devfeed_core import article_refresh_processing as processing
from devfeed_core import article_refresh_publication as publication
from devfeed_core.article_refresh_types import (
    RefreshContent,
    RefreshFields,
    RefreshTag,
    RefreshTopic,
)
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleRefresh,
    ArticleReview,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy import inspect
from sqlalchemy.orm import Session, make_transient_to_detached

from tests.support import article_maintenance_unit as maintenance_unit
from tests.support.article_maintenance_unit import prepared as prepared


@pytest.mark.parametrize(
    ("method", "text", "expected", "provenance"),
    [
        (None, None, "Longer publisher summary", "publisher"),
        ("html", "", "Longer publisher summary", "publisher"),
        ("description", "Short teaser", "Longer publisher summary", "publisher"),
        ("description", "Longer publisher summary", "Longer publisher summary", "description"),
        (
            "description",
            "Complete extracted description from the page",
            "Complete extracted description from the page",
            "description",
        ),
        ("html", "Page body", "Page body", "html"),
    ],
)
def test_analysis_uses_the_best_private_source_text_without_changing_public_fields(
    prepared, method, text, expected, provenance
):
    public, _, inputs, _ = prepared
    original = public.summary
    draft = inputs.draft.model_copy(deep=True)
    draft.fields.summary = "Longer publisher summary"
    draft.content = (
        RefreshContent(
            text=text,
            content_hash="c" * 64,
            url=public.canonical_url,
            method=method,
            retrieved_at=maintenance_unit.NOW,
        )
        if method
        else None
    )
    snapshot = processing.candidate_snapshot(public, draft)
    assert snapshot == {
        "url": public.canonical_url,
        "title": draft.fields.title,
        "source_summary": "Longer publisher summary",
        "text": expected,
        "text_source": provenance,
    }
    assert public.summary == original


@pytest.mark.parametrize("outcome", ["absent", "insufficient_evidence"])
def test_incomplete_analysis_cannot_replace_explicit_private_assignments(prepared, outcome):
    _, _, inputs, _ = prepared
    draft = inputs.draft.model_copy(deep=True)
    if outcome == "absent":
        draft.analysis = None
    else:
        draft.analysis = maintenance_unit.ready_analysis(
            draft.topics[0].topic_id,
            outcome="insufficient_evidence",
            developer_relevance="uncertain",
            page_kind="uncertain",
            topics=[],
        )
    tags = [RefreshTag(tag_id=uuid.uuid4(), origin="source")]
    draft.tags = tags
    assert processing.candidate_assignments(draft) == (draft.topics, tags)


def test_ready_analysis_preserves_manual_topics_and_source_tags_and_replaces_old_ai(prepared):
    _, _, inputs, _ = prepared
    draft = inputs.draft.model_copy(deep=True)
    manual = draft.topics[0]
    stale_id, supporting_id = uuid.uuid4(), uuid.uuid4()
    draft.topics.append(
        RefreshTopic(
            topic_id=stale_id, role="primary", relevance=1, evidence="Old AI topic", origin="ai"
        )
    )
    manual_tag, source_tag, old_ai_tag, new_ai_tag = [uuid.uuid4() for _ in range(4)]
    draft.tags = [
        RefreshTag(tag_id=manual_tag, origin="manual"),
        RefreshTag(tag_id=source_tag, origin="source"),
        RefreshTag(tag_id=old_ai_tag, origin="ai"),
    ]
    draft.analysis = maintenance_unit.ready_analysis(
        manual.topic_id,
        topics=[
            {
                "topic_id": str(manual.topic_id),
                "role": "supporting",
                "relevance": 0.4,
                "evidence": "AI cannot override the operator",
            },
            {
                "topic_id": str(supporting_id),
                "role": "supporting",
                "relevance": 0.9,
                "evidence": "Strong new AI evidence",
            },
        ],
        tags=[
            {"id": str(value), "evidence": "Explicit source label"}
            for value in (source_tag, new_ai_tag)
        ],
    )
    topics, tags = processing.candidate_assignments(draft)
    by_topic, by_tag = (
        {item.topic_id: item for item in topics},
        {item.tag_id: item for item in tags},
    )
    assert set(by_topic) == {manual.topic_id, supporting_id}
    assert by_topic[manual.topic_id] == manual
    assert by_topic[supporting_id].role == "primary" and by_topic[supporting_id].origin == "ai"
    assert {key: value.origin for key, value in by_tag.items()} == {
        manual_tag: "manual",
        source_tag: "source",
        new_ai_tag: "ai",
    }
    assert [item.topic_id for item in topics] == sorted(by_topic)
    assert [item.tag_id for item in tags] == sorted(by_tag)


def test_private_view_has_staged_fields_and_provenance_but_keeps_public_identity(prepared):
    public, owner, inputs, _ = prepared
    before = (public.title, public.ai_title, public.publication_status, public.slug)
    inputs.draft.fields.title = "Private extracted heading"
    owner.image_url = "https://publisher.example/private-image.png"
    session = Mock(spec=Session)
    session.scalars.side_effect = [[public.topic_links[0].topic], []]
    private = processing.candidate_view(session, public, owner, inputs.draft, inputs.facts)
    assert inspect(private).transient and session.add.call_count == session.flush.call_count == 0
    assert private.id == public.id and private.slug == public.slug
    assert private.publication_status == "unpublished" and private.review_status == "approved"
    assert (
        private.title == "Private extracted heading"
        and private.ai_title == inputs.draft.analysis.ai_title
    )
    assert private.image_url == owner.image_url and public.image_url is None
    assert private.classification_provenance["analysis_id"] == str(inputs.facts["analysis"].id)
    assert private.classification_provenance["generated_at"] == maintenance_unit.NOW.isoformat()
    assert private.classification_provenance["input_hash"] == inputs.input_hash
    assert before == (public.title, public.ai_title, public.publication_status, public.slug)


@pytest.mark.parametrize("missing", ["result", "usable_result", "job"])
def test_incomplete_private_analysis_does_not_claim_new_generation_provenance(prepared, missing):
    public, owner, inputs, _ = prepared
    draft, facts = inputs.draft.model_copy(deep=True), dict(inputs.facts)
    if missing == "result":
        draft.analysis = None
    elif missing == "usable_result":
        draft.analysis = maintenance_unit.ready_analysis(
            draft.topics[0].topic_id, outcome="insufficient_evidence", page_kind="uncertain"
        )
    else:
        facts["analysis"] = None
    session = Mock(spec=Session)
    session.scalars.side_effect = [[public.topic_links[0].topic], []]
    private = processing.candidate_view(session, public, owner, draft, facts)
    assert private.classification_provenance == public.classification_provenance
    assert private.ai_title == public.ai_title and private.ai_summary == public.ai_summary
    assert private.publication_status == "unpublished" and public.publication_status == "published"
    session.add.assert_not_called()


@pytest.mark.parametrize("slot", ["enrichment", "analysis", "discovery", "storage"])
@pytest.mark.parametrize("authority", ["ordinary", "same_owner", "promoted_pruned"])
def test_selected_facts_reuse_only_approved_or_same_owner_artifacts(prepared, slot, authority):
    public, owner, inputs, _ = prepared
    model = {"enrichment": ArticleEnrichmentJob, "analysis": ArticleAnalysisJob}.get(
        slot, ArticleImageJob
    )
    values = (
        {"operation": "discover" if slot == "discovery" else "store"}
        if model is ArticleImageJob
        else {}
    )
    previous = ArticleRefresh(
        id=uuid.uuid4(),
        article_id=public.id,
        status="promoted",
        payload={},
        payload_pruned_at=maintenance_unit.NOW,
    )
    binding = (
        None if authority == "ordinary" else owner.id if authority == "same_owner" else previous.id
    )
    job = model(
        id=uuid.uuid4(), article_id=public.id, refresh_id=binding, status="succeeded", **values
    )
    for name in ("enrichment", "analysis", "discovery", "storage"):
        setattr(owner, f"{name}_job_id", job.id if name == slot else None)
    setattr(previous, f"{slot}_job_id", job.id)
    session = Mock(spec=Session)
    session.get.side_effect = lambda kind, identifier, **_: (
        previous if kind is ArticleRefresh else job
    )
    facts = processing.refresh_facts(session, owner, inputs.draft, lightweight=True)
    assert facts[slot] is job and job.refresh_id == binding
    assert previous.payload == {} and previous.payload_pruned_at == maintenance_unit.NOW
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("slot", ["enrichment", "analysis", "discovery", "storage"])
@pytest.mark.parametrize(
    "changed",
    ["foreign_article", "owner_missing", "owner_active", "foreign_owner_article", "unselected"],
)
def test_private_facts_reject_unapproved_artifacts_even_if_other_work_was_promoted(
    prepared, slot, changed
):
    public, owner, inputs, _ = prepared
    model = {"enrichment": ArticleEnrichmentJob, "analysis": ArticleAnalysisJob}.get(
        slot, ArticleImageJob
    )
    values = (
        {"operation": "discover" if slot == "discovery" else "store"}
        if model is ArticleImageJob
        else {}
    )
    previous = ArticleRefresh(id=uuid.uuid4(), article_id=public.id, status="promoted")
    job = model(
        id=uuid.uuid4(), article_id=public.id, refresh_id=previous.id, status="succeeded", **values
    )
    for name in ("enrichment", "analysis", "discovery", "storage"):
        setattr(owner, f"{name}_job_id", job.id if name == slot else None)
    setattr(previous, f"{slot}_job_id", job.id)
    if changed == "foreign_article":
        job.article_id = uuid.uuid4()
    elif changed == "owner_active":
        previous.status = "active"
    elif changed == "foreign_owner_article":
        previous.article_id = uuid.uuid4()
    elif changed == "unselected":
        setattr(previous, f"{slot}_job_id", uuid.uuid4())
    session = Mock(spec=Session)
    session.get.side_effect = lambda kind, identifier, **_: (
        (None if changed == "owner_missing" else previous) if kind is ArticleRefresh else job
    )
    with pytest.raises(OperationConflict):
        processing.refresh_facts(session, owner, inputs.draft)
    session.add.assert_not_called()


@pytest.mark.parametrize("slot", ["discovery", "storage"])
def test_image_slot_cannot_treat_a_different_operation_as_approved_evidence(prepared, slot):
    public, owner, inputs, _ = prepared
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=public.id,
        refresh_id=owner.id,
        operation="store" if slot == "discovery" else "discover",
    )
    for name in ("enrichment", "analysis", "discovery", "storage"):
        setattr(owner, f"{name}_job_id", job.id if name == slot else None)
    session = Mock(spec=Session)
    session.get.return_value = job
    with pytest.raises(OperationConflict, match="wrong image operation"):
        processing.refresh_facts(session, owner, inputs.draft)


@pytest.mark.parametrize(
    "unavailable", ["missing", "promoted", "cancelled", "superseded", "pruned", "missing_article"]
)
def test_preparation_refuses_inactive_or_missing_subject_before_loading_candidate_work(
    prepared, unavailable
):
    public, owner, _, _ = prepared
    if unavailable == "pruned":
        owner.payload_pruned_at = maintenance_unit.NOW
    elif unavailable not in {"missing", "missing_article"}:
        owner.status = unavailable
    session = Mock(spec=Session)
    session.get.side_effect = lambda model, identifier: (
        (None if unavailable == "missing" else owner)
        if model is ArticleRefresh
        else (None if unavailable == "missing_article" else public)
    )
    expected = RecordNotFound if unavailable == "missing_article" else OperationConflict
    with pytest.raises(expected):
        processing.prepare_refresh_inputs(session, owner.id)
    session.scalars.assert_not_called()


@pytest.mark.parametrize("changed", ["unchanged", "title", "summary", "body", "catalog"])
def test_prepared_hashes_track_private_evidence_and_catalog_without_refreshing_owner_authority(
    prepared, monkeypatch, changed
):
    public, owner, initial, _ = prepared
    draft = initial.draft.model_copy(deep=True)
    if changed == "title":
        draft.fields.title = "A different private source headline"
    elif changed == "summary":
        draft.fields.summary = "A different private extraction describes Angular routing state."
    elif changed == "body":
        draft.content = RefreshContent(
            text="Private page body explains Angular navigation state.",
            content_hash="c" * 64,
            url=public.canonical_url,
            method="html",
            retrieved_at=maintenance_unit.NOW,
        )
    owner.payload = draft.model_dump(mode="json")
    taxonomy = deepcopy(initial.taxonomy)
    if changed == "catalog":
        taxonomy["topics"][0]["name"] = "Angular application navigation"
    loader = Mock(return_value=taxonomy)
    monkeypatch.setattr(processing, "catalog", loader)
    records = {
        (ArticleRefresh, owner.id): owner,
        (Article, public.id): public,
        (ArticleEnrichmentJob, owner.enrichment_job_id): initial.facts["enrichment"],
        (ArticleAnalysisJob, owner.analysis_job_id): initial.facts["analysis"],
    }
    session = Mock(spec=Session)
    session.get.side_effect = lambda model, identifier, **_: records[model, identifier]
    session.scalars.side_effect = [[public.topic_links[0].topic], []]
    result = processing.prepare_refresh_inputs(session, owner.id)
    assert result.refresh_id == owner.id and result.version == owner.version == 1
    assert inspect(result.article).transient and result.article.id == public.id
    assert result.input_hash == analysis.snapshot_hash(result.snapshot)
    assert result.catalog_hash == analysis.snapshot_hash(result.candidates)
    assert (result.input_hash != initial.input_hash) is (changed in {"title", "summary", "body"})
    assert (result.catalog_hash != initial.catalog_hash) is (changed == "catalog")
    assert owner.source_hash == initial.input_hash and owner.status == "active"
    assert public.publication_status == "published"
    if changed == "title":
        assert public.title != draft.fields.title
    loader.assert_called_once_with(session)
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_initial_preparation_can_load_catalog_without_attaching_or_publishing_candidate(
    prepared, monkeypatch
):
    public, _, initial, base = prepared
    loader = Mock(return_value=initial.taxonomy)
    monkeypatch.setattr(processing, "catalog", loader)
    session = Mock(spec=Session)
    session.scalars.side_effect = [[public.topic_links[0].topic], []]
    result = processing.prepare_initial_refresh_inputs(session, public, base, initial.facts)
    assert result.snapshot == initial.snapshot and result.input_hash == initial.input_hash
    assert result.candidates == initial.candidates and result.catalog_hash == initial.catalog_hash
    assert inspect(result.article).transient and public.publication_status == "published"
    loader.assert_called_once_with(session)
    session.add.assert_not_called()


def test_private_extraction_fact_is_transient_and_does_not_read_the_public_body(prepared):
    public, owner, inputs, _ = prepared
    draft = inputs.draft.model_copy(deep=True)
    draft.content = RefreshContent(
        text="Only the staged extraction is used as the private body.",
        content_hash="c" * 64,
        url=public.canonical_url,
        method="html",
        retrieved_at=maintenance_unit.NOW,
    )
    for slot in ("enrichment", "analysis", "discovery", "storage"):
        setattr(owner, f"{slot}_job_id", None)
    session = Mock(spec=Session)
    facts = processing.refresh_facts(session, owner, draft)
    assert facts["content"].text == draft.content.text
    assert facts["content"].article_id == owner.article_id
    assert inspect(facts["content"]).transient
    session.get.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "new",
        "same_active",
        "other_active",
        "wrong_selected",
        "wrong_article",
        "ai_disabled",
        "source_withdrawn",
        "article_missing",
        "owner_missing",
        "owner_version",
        "owner_input",
    ],
)
def test_private_analysis_admission_rechecks_authority_without_repreparing_body_under_locks(
    prepared, monkeypatch, case
):
    public, owner, inputs, _ = prepared
    reprepare = Mock(side_effect=AssertionError("Body/ranking must be prepared before locks"))
    monkeypatch.setattr(processing, "prepare_refresh_inputs", reprepare)
    approved = Mock(return_value=case != "source_withdrawn")
    monkeypatch.setattr(article_jobs, "approved_sources", approved)
    lookup = Mock(return_value=None if case == "owner_missing" else owner)
    monkeypatch.setattr(article_refresh, "refresh_for_article", lookup)
    if case == "ai_disabled":
        monkeypatch.setattr(get_settings(), "ai_enabled", False)
    elif case == "owner_version":
        owner.version += 1
    elif case == "owner_input":
        owner.source_hash = "f" * 64
    active = None
    if case in {"same_active", "other_active", "wrong_selected"}:
        active = ArticleAnalysisJob(
            id=owner.analysis_job_id if case != "wrong_selected" else uuid.uuid4(),
            article_id=public.id,
            refresh_id=uuid.uuid4() if case == "other_active" else owner.id,
            status="queued",
        )
    session = Mock(spec=Session)
    session.scalar.side_effect = [None if case == "article_missing" else public, active]

    def assign_new_job_identifier():
        session.add.call_args.args[0].id = uuid.uuid4()

    session.flush.side_effect = assign_new_job_identifier
    call = dict(
        prepared=inputs, expected_article_id=uuid.uuid4() if case == "wrong_article" else public.id
    )
    if case in {"new", "same_active"}:
        job = processing.request_refresh_analysis(session, owner.id, **call)
        assert job.article_id == public.id and job.refresh_id == owner.id
        if case == "same_active":
            assert job is active
            session.add.assert_not_called()
        else:
            assert (
                job.input_snapshot == inputs.snapshot and job.input_snapshot is not inputs.snapshot
            )
            assert job.input_hash == inputs.input_hash and job.catalog_hash == inputs.catalog_hash
            assert job.catalog_snapshot == inputs.candidates
            assert owner.analysis_job_id == job.id and owner.version == 2
            session.add.assert_called_once_with(job)
    else:
        expected = RecordNotFound if case == "article_missing" else OperationConflict
        with pytest.raises(expected):
            processing.request_refresh_analysis(session, owner.id, **call)
        session.add.assert_not_called()
    assert public.publication_status == "published" and inputs.public is public
    reprepare.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("target", ["enrichment", "analysis"])
def test_complete_private_output_and_real_publication_policy_agree_it_is_ready(prepared, target):
    _, _, inputs, _ = prepared
    assert publication.replacement_ready(inputs, target) == (True, ())


@pytest.mark.parametrize("target", ["enrichment", "analysis"])
@pytest.mark.parametrize("change", ["missing", "foreign_owner", "queued", "failed"])
def test_replacement_requires_success_of_its_exact_private_target(prepared, target, change):
    _, _, inputs, _ = prepared
    job = inputs.facts[target]
    if change == "missing":
        inputs.facts[target] = None
    elif change == "foreign_owner":
        job.refresh_id = uuid.uuid4()
    else:
        job.status = change
    ready, reasons = publication.replacement_ready(inputs, target)
    assert not ready and "maintenance_target_incomplete" in reasons


@pytest.mark.parametrize(
    "change", ["missing", "invalid", "changed", "stale_input", "withdrawn_source", "unapproved"]
)
def test_valid_job_completion_cannot_bypass_output_receipt_or_current_publication_policy(
    prepared, change
):
    _, _, inputs, _ = prepared
    expected = {
        "missing": "private_analysis_output_missing",
        "invalid": "private_analysis_output_invalid",
        "changed": "private_analysis_output_changed",
        "stale_input": "current_analysis_required",
        "withdrawn_source": "no_approved_source",
        "unapproved": "not_approved",
    }[change]
    if change == "missing":
        inputs.draft.analysis = None
    elif change == "invalid":
        inputs.facts["analysis"].result = {"outcome": "invalid"}
    elif change == "changed":
        inputs.facts["analysis"].result = {
            **inputs.facts["analysis"].result,
            "ai_description": "A different approved replacement description.",
        }
    elif change == "stale_input":
        inputs.facts["analysis"].input_hash = "f" * 64
    elif change == "withdrawn_source":
        inputs.article.origins[0].source.approval_status = "rejected"
    else:
        inputs.article.review_status = "pending"
    ready, reasons = publication.replacement_ready(inputs, "analysis")
    assert not ready and expected in reasons


@pytest.mark.parametrize(
    ("full", "policy", "enabled", "approved", "human_review", "expected"),
    [
        (False, "manual", True, True, False, False),
        (False, "preview", True, True, False, False),
        (False, "auto", True, True, False, True),
        (False, "auto", True, True, True, False),
        (False, "auto", False, True, False, False),
        (False, "auto", True, False, False, False),
        (True, "manual", True, True, True, True),
        (True, "preview", True, True, True, True),
        (True, "auto", True, True, True, True),
        (True, "auto", False, True, False, False),
        (True, "auto", True, False, False, False),
    ],
)
def test_automatic_replacement_respects_live_source_policy_and_existing_human_review(
    prepared, monkeypatch, full, policy, enabled, approved, human_review, expected
):
    public, _, _, _ = prepared
    source = public.origins[0].source
    source.publication_policy, source.enabled = policy, enabled
    source.approval_status = "approved" if approved else "rejected"
    monkeypatch.setattr(get_settings(), "full_automation", full)
    session = Mock(spec=Session)
    session.scalar.return_value = uuid.uuid4() if human_review else None
    assert publication.automatic_replacement_allowed(session, public) is expected
    if full or not enabled or not approved or policy != "auto":
        session.scalar.assert_not_called()
    else:
        session.scalar.assert_called_once()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def public_projection(article):
    fields = [
        *RefreshFields.model_fields,
        "id",
        "slug",
        "feed_at",
        "published_to_feed_at",
        "discovered_at",
        "publication_status",
        "review_status",
        "editorial_revision",
        "classification_provenance",
        "image_url",
        "managed_image",
        "sponsor_names",
    ]
    return {name: deepcopy(getattr(article, name)) for name in fields}


@pytest.fixture
def promotion(prepared, monkeypatch):
    public, owner, inputs, base = prepared
    session = Mock(spec=Session)
    session.scalar.side_effect = [public, owner, None]
    authority = Mock(return_value=True)
    digest = Mock(return_value=base.base_hash)
    catalog = Mock(return_value=True)
    classifications = Mock(return_value={"approved_assignment": "current private analysis"})
    tags = Mock()
    monkeypatch.setattr(publication, "approved_sources", authority)
    monkeypatch.setattr(publication, "approved_digest", digest)
    monkeypatch.setattr(publication, "snapshot_current", catalog)
    monkeypatch.setattr(publication, "lock_topics", Mock())
    monkeypatch.setattr(publication, "replace_classifications", classifications)
    monkeypatch.setattr(publication, "attach_source_tags", tags)
    monkeypatch.setattr(publication, "utcnow", lambda: maintenance_unit.NOW)
    ready, reasons = publication.replacement_ready(inputs, owner.target)
    assert ready and not reasons
    return SimpleNamespace(
        public=public,
        owner=owner,
        session=session,
        authority=authority,
        digest=digest,
        catalog=catalog,
        classifications=classifications,
        tags=tags,
        prepared=publication.PreparedPromotion(inputs, base.base_hash, ready, reasons),
        base=base,
    )


@pytest.mark.parametrize("actor", [None, {"subject": "reviewer"}])
@pytest.mark.parametrize(
    "invalid", ["missing_owner", "inactive", "version", "catalog", "not_ready"]
)
def test_unavailable_replacement_never_writes_any_public_fields_or_audit(promotion, actor, invalid):
    p = promotion
    before = public_projection(p.public)
    if invalid == "missing_owner":
        p.session.scalar.side_effect = [p.public, None]
    elif invalid == "inactive":
        p.owner.status = "cancelled"
    elif invalid == "version":
        p.owner.version += 1
    elif invalid == "catalog":
        p.catalog.return_value = False
    else:
        p.prepared = replace(p.prepared, ready=False, reasons=("analysis_pending",))
    with pytest.raises(OperationConflict):
        publication.commit_promotion(p.session, p.prepared, actor=actor)
    assert public_projection(p.public) == before
    p.classifications.assert_not_called()
    p.tags.assert_not_called()
    p.session.add.assert_not_called()
    p.session.flush.assert_not_called()


@pytest.mark.parametrize("actor", [None, {"subject": "reviewer"}])
@pytest.mark.parametrize(
    "changed",
    [
        "source",
        "missing_article",
        "withdrawn",
        "review",
        "revision",
        "source_hash",
        "base_hash",
        "approved_digest",
    ],
)
def test_replacement_cannot_undo_new_public_authority_or_approval_changes(
    promotion, actor, changed
):
    p = promotion
    if changed == "source":
        p.authority.return_value = False
    elif changed == "missing_article":
        p.session.scalar.side_effect = [None, p.owner]
    elif changed == "withdrawn":
        p.public.publication_status = "unpublished"
    elif changed == "review":
        p.public.review_status = "rejected"
    elif changed == "revision":
        p.public.editorial_revision += 1
    elif changed == "source_hash":
        p.owner.source_hash = "f" * 64
    elif changed == "base_hash":
        p.owner.base_hash = "f" * 64
    else:
        p.digest.return_value = "f" * 64
    before = public_projection(p.public)
    if actor is None:
        assert publication.commit_promotion(p.session, p.prepared) is p.owner
        assert (
            p.owner.status == "superseded"
            and p.owner.completed_at == maintenance_unit.NOW
            and p.owner.version == 2
        )
    else:
        with pytest.raises(OperationConflict, match="authority changed"):
            publication.commit_promotion(p.session, p.prepared, actor=actor)
        assert p.owner.status == "active" and p.owner.version == 1
    assert public_projection(p.public) == before
    p.session.add.assert_not_called()
    p.session.get.assert_not_called()
    p.classifications.assert_not_called()
    p.tags.assert_not_called()


@pytest.mark.parametrize("existing", ["active", "review"])
@pytest.mark.parametrize("reason", ["manual_source", "human_review"])
def test_automatic_replacement_waits_for_review_without_modifying_approved_projection(
    promotion, existing, reason
):
    p = promotion
    p.owner.status = existing
    if reason == "manual_source":
        p.public.origins[0].source.publication_policy = "manual"
    else:
        p.session.scalar.side_effect = [p.public, p.owner, uuid.uuid4()]
    before = public_projection(p.public)
    assert publication.commit_promotion(p.session, p.prepared) is p.owner
    assert p.owner.status == "review" and p.owner.version == (2 if existing == "active" else 1)
    assert p.owner.approved_version is None and public_projection(p.public) == before
    p.session.add.assert_not_called()
    p.classifications.assert_not_called()
    p.tags.assert_not_called()


@pytest.mark.parametrize(
    "actor", [None, {"name": "Editor", "subject": "reviewer"}, {"subject": "reviewer"}]
)
@pytest.mark.parametrize("content_case", ["absent", "new", "changed", "unchanged"])
def test_allowed_replacement_copies_one_complete_projection_and_keeps_public_identity_and_history(
    promotion, actor, content_case
):
    p = promotion
    before = public_projection(p.public)
    inputs = p.prepared.inputs
    existing_content = None
    if content_case != "absent":
        draft = inputs.draft.model_copy(deep=True)
        draft.content = RefreshContent(
            text="Approved replacement explains practical Angular navigation diagnostics.",
            content_hash="c" * 64,
            url=p.public.canonical_url + "?extraction=2",
            method="html",
            retrieved_at=maintenance_unit.NOW,
        )
        snapshot = processing.candidate_snapshot(p.public, draft)
        facts = inputs.facts
        facts["analysis"].input_snapshot = snapshot
        facts["analysis"].input_hash = analysis.snapshot_hash(snapshot)
        facts["analysis"].catalog_snapshot = analysis.analysis_candidates(inputs.taxonomy, snapshot)
        facts["analysis"].catalog_hash = analysis.snapshot_hash(facts["analysis"].catalog_snapshot)
        base = replace(p.base, draft=draft, source_hash=facts["analysis"].input_hash)
        reader = Mock(spec=Session)
        reader.scalars.side_effect = [[p.public.topic_links[0].topic], []]
        inputs = processing.prepare_initial_refresh_inputs(
            reader, p.public, base, facts, inputs.taxonomy
        )
        p.prepared = replace(p.prepared, inputs=inputs)
        p.owner.source_hash, p.owner.payload = inputs.input_hash, draft.model_dump(mode="json")
        if content_case != "new":
            existing_content = ArticleContent(
                article_id=p.public.id,
                text=draft.content.text
                if content_case == "unchanged"
                else "Old approved extraction",
                content_hash=draft.content.content_hash
                if content_case == "unchanged"
                else "a" * 64,
                url=p.public.canonical_url,
                method="description",
                retrieved_at=maintenance_unit.NOW,
            )
            make_transient_to_detached(existing_content)
        p.session.get.return_value = existing_content
    source_tag, manual_tag = uuid.uuid4(), uuid.uuid4()
    inputs.draft.tags = [
        RefreshTag(tag_id=source_tag, origin="source"),
        RefreshTag(tag_id=manual_tag, origin="manual"),
    ]
    if actor is not None:
        # Explicit consent can approve a ready replacement from a manual source;
        # source approval/current base checks above remain mandatory.
        p.public.origins[0].source.publication_policy = "manual"
    result = publication.commit_promotion(
        p.session, p.prepared, actor=actor, note="Reviewed replacement"
    )
    assert result is p.owner and result.status == "promoted" and result.version == 2
    assert result.completed_at == maintenance_unit.NOW and result.approved_version == (
        1 if actor is not None else None
    )
    if actor is not None:
        assert result.reviewed_by == actor and result.reviewed_at == maintenance_unit.NOW
        assert result.review_note == "Reviewed replacement"
    for name in RefreshFields.model_fields:
        assert getattr(p.public, name) == getattr(inputs.article, name)
    assert p.public.classification_provenance == {
        **inputs.article.classification_provenance,
        **p.classifications.return_value,
    }
    assert p.public.editorial_revision == before["editorial_revision"] + 1
    for name in (
        "id",
        "slug",
        "feed_at",
        "published_to_feed_at",
        "discovered_at",
        "publication_status",
        "review_status",
        "sponsor_names",
    ):
        assert getattr(p.public, name) == before[name]
    p.tags.assert_called_once_with(p.session, p.public.id, [source_tag])
    p.classifications.assert_called_once_with(
        p.session, p.public, inputs.draft.analysis, origin="ai"
    )
    added = [call.args[0] for call in p.session.add.call_args_list]
    audits = [value for value in added if isinstance(value, ArticleReview)]
    assert len(audits) == 1 and len(added) == (2 if content_case == "new" else 1)
    audit = audits[0]
    assert audit.action == "refresh" and audit.revision == p.public.editorial_revision
    assert audit.automation == {
        "kind": "maintenance_promotion",
        "refresh_id": str(p.owner.id),
        "base_revision": p.owner.base_revision,
    }
    assert audit.actor == (
        actor.get("name") or actor.get("subject") or "Administrator"
        if actor is not None
        else "DevFeed maintenance"
    )
    assert audit.note == "Reviewed replacement"
    if content_case == "absent":
        p.session.get.assert_not_called()
    else:
        content = existing_content or next(
            value for value in added if isinstance(value, ArticleContent)
        )
        assert content.text == inputs.draft.content.text
        assert content.content_hash == inputs.draft.content.content_hash
        assert content.url == inputs.draft.content.url and content.method == "html"
        assert content.retrieved_at == maintenance_unit.NOW
        if content_case == "unchanged":
            assert not inspect(content).attrs.text.history.has_changes()
    p.session.flush.assert_called_once_with()
    p.session.commit.assert_not_called()


def test_enrichment_only_replacement_preserves_the_approved_analysis_and_assignments(promotion):
    p = promotion
    initial = p.prepared.inputs
    draft = initial.draft.model_copy(deep=True)
    draft.analysis = None
    initial.facts["analysis"].refresh_id = None
    base = replace(p.base, draft=draft)
    reader = Mock(spec=Session)
    reader.scalars.side_effect = [[p.public.topic_links[0].topic], []]
    inputs = processing.prepare_initial_refresh_inputs(
        reader, p.public, base, initial.facts, initial.taxonomy
    )
    p.owner.target = "enrichment"
    p.prepared = replace(p.prepared, inputs=inputs)
    assert publication.replacement_ready(inputs, "enrichment") == (True, ())
    before = public_projection(p.public)
    assert publication.commit_promotion(p.session, p.prepared).status == "promoted"
    assert p.public.classification_provenance == before["classification_provenance"]
    assert p.public.ai_title == before["ai_title"] and p.public.ai_summary == before["ai_summary"]
    assert p.public.publication_status == "published" and p.public.slug == before["slug"]
    p.classifications.assert_not_called()
    p.session.get.assert_not_called()


@pytest.mark.parametrize("actor", [None, {"name": "Reviewer " * 40}])
def test_maintenance_audit_has_bounded_actor_and_clear_default_note(promotion, actor):
    p = promotion
    assert publication.commit_promotion(p.session, p.prepared, actor=actor).status == "promoted"
    audit = p.session.add.call_args.args[0]
    assert (
        isinstance(audit, ArticleReview)
        and audit.note == "Promote the complete private replacement"
    )
    assert audit.actor == (actor["name"][:200] if actor else "DevFeed maintenance")


@pytest.mark.parametrize("ready", [False, True])
def test_promotion_preparation_records_current_base_and_real_policy_result(
    prepared, monkeypatch, ready
):
    _, owner, inputs, _ = prepared
    if not ready:
        inputs.facts["analysis"].status = "failed"
    loader = Mock(return_value=inputs)
    monkeypatch.setattr(publication, "prepare_refresh_inputs", loader)
    session = Mock(spec=Session)
    session.get.return_value = owner
    result = publication.prepare_promotion(session, owner.id)
    assert result.inputs is inputs and result.base_hash == owner.base_hash and result.ready is ready
    assert bool(result.reasons) is (not ready)
    session.add.assert_not_called()


@pytest.mark.parametrize("changed", ["none", "article", "version"])
def test_explicit_review_is_bound_to_requested_article_and_candidate_version(
    promotion, monkeypatch, changed
):
    p = promotion
    prepare = Mock(return_value=p.prepared)
    commit = Mock(return_value=p.owner)
    monkeypatch.setattr(publication, "prepare_promotion", prepare)
    monkeypatch.setattr(publication, "commit_promotion", commit)
    article_id = uuid.uuid4() if changed == "article" else p.public.id
    version = 2 if changed == "version" else 1
    actor = {"subject": "reviewer"}
    if changed == "none":
        assert (
            publication.approve_refresh(
                p.session, article_id, p.owner.id, version, actor, "Approved"
            )
            is p.owner
        )
        commit.assert_called_once_with(p.session, p.prepared, actor=actor, note="Approved")
    else:
        with pytest.raises(OperationConflict, match="changed"):
            publication.approve_refresh(
                p.session, article_id, p.owner.id, version, actor, "Approved"
            )
        commit.assert_not_called()


@pytest.mark.parametrize("result", ["incomplete", "promoted", "review", "superseded", "conflict"])
def test_background_promotion_closes_preparation_before_atomic_command_and_defers_conflicts(
    promotion, monkeypatch, result
):
    p, events = promotion, []

    @contextmanager
    def phase(name):
        events.append(name)
        try:
            yield p.session
        finally:
            events.append(name + "_closed")

    factory = Mock(return_value=phase("read"))
    factory.begin.return_value = phase("write")
    prepared_result = replace(p.prepared, ready=False) if result == "incomplete" else p.prepared
    monkeypatch.setattr(publication, "prepare_promotion", Mock(return_value=prepared_result))

    def commit(session, prepared):
        assert events == ["read", "read_closed", "write"]
        assert session is p.session and prepared is p.prepared
        if result == "conflict":
            raise OperationConflict("Authority changed after preparation")
        p.owner.status = result
        return p.owner

    command = Mock(side_effect=commit)
    monkeypatch.setattr(publication, "commit_promotion", command)
    assert publication.promote_processed_refresh(factory, p.owner.id) == (
        "pending" if result in {"incomplete", "conflict"} else result
    )
    if result == "incomplete":
        command.assert_not_called()
        factory.begin.assert_not_called()
    else:
        command.assert_called_once()
        assert events[-1] == "write_closed"
