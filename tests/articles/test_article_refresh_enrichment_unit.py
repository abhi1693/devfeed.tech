"""Extracted replacements stay private across leases, authority changes and retries.

Repository boundaries supply explicit rows. Real extraction merge, draft bounds,
source hashes and lease completion run here; native cases prove SQL isolation.
"""

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import analysis_tasks, article_tasks
from devfeed_aggregator import article_refresh_tasks as worker
from devfeed_aggregator.article_pages import PageArticle
from devfeed_core import article_refresh_processing as processing
from devfeed_core.analysis import snapshot_hash
from devfeed_core.article_refresh_types import (
    RefreshContent,
    RefreshDraft,
    RefreshTag,
    RefreshTopic,
)
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import Article, ArticleRefresh
from sqlalchemy.orm import Session

from tests.support import article_maintenance_unit as support
from tests.support.article_maintenance_unit import prepared as prepared


def page(**changes):
    return PageArticle(
        **{
            "title": "Extracted Angular navigation tutorial",
            "summary": "Inspect Angular routing decisions using page extraction evidence.",
            "author": "Publisher writer",
            "published_at": support.NOW - timedelta(days=1),
            "image_url": None,
            "language": "en",
            "text_source": "html",
            "text": "Angular navigation exposes useful route state for application diagnostics.",
            "tags": [],
            "evidence": {"method": "html", "paywalled": False},
            **changes,
        }
    )


def public_values(article):
    return {
        column.name: deepcopy(getattr(article, column.name)) for column in Article.__table__.columns
    }


@pytest.fixture
def extraction(prepared, monkeypatch):
    public, owner, inputs, _ = prepared
    owner.target = "enrichment"
    job = inputs.facts["enrichment"]
    token = job.lease_token = uuid.uuid4()
    job.status, job.outcome, job.error = "running", None, "Previous transport error"
    job.lease_until = support.NOW + timedelta(minutes=5)
    job.changed_fields = []
    read, write = Mock(spec=Session), Mock(spec=Session)
    read.get.side_effect = lambda model, identifier, **_: (
        owner if model is ArticleRefresh else public
    )
    write.scalar.return_value = public
    factory = support.WorkerSessions(read, write)
    authority = Mock(return_value=[public.origins[0].source])
    lease, current = Mock(return_value=job), Mock(return_value=owner)
    labels = Mock(return_value=({}, 0))
    resume = Mock()
    original_merge = article_tasks.page_values
    merges = []

    def merge(subject, extracted):
        assert not factory.writing
        merges.append(subject)
        return original_merge(subject, extracted)

    def continued(actual_factory, identifier):
        assert actual_factory is factory and identifier == owner.id and not factory.writing
        assert job.status == "succeeded" and job.lease_token is None

    resume.side_effect = continued
    monkeypatch.setattr(article_tasks, "page_values", merge)
    monkeypatch.setattr(worker, "approved_sources", authority)
    monkeypatch.setattr(worker, "owned_job", lease)
    monkeypatch.setattr(worker, "refresh_for_article", current)
    monkeypatch.setattr(worker, "resolve_source_tags", labels)
    monkeypatch.setattr(worker, "utcnow", lambda: support.NOW)
    monkeypatch.setattr(processing, "resume_refresh", resume)
    return SimpleNamespace(
        public=public,
        owner=owner,
        job=job,
        token=token,
        inputs=inputs,
        read=read,
        write=write,
        factory=factory,
        authority=authority,
        lease=lease,
        current=current,
        labels=labels,
        resume=resume,
        merges=merges,
        article_id=public.id,
        refresh_id=owner.id,
        url=public.canonical_url,
    )


def apply(runtime, extracted=None):
    return worker.apply_refresh_enrichment(
        runtime.factory,
        runtime.job.id,
        runtime.token,
        runtime.article_id,
        runtime.refresh_id,
        runtime.url,
        FetchResult(200, b"", runtime.url + "?redirected=1"),
        extracted or page(),
    )


def test_enrichment_writes_bounded_private_body_metadata_and_source_tags_after_exact_guards(
    extraction,
):
    r = extraction
    before = public_values(r.public)
    original_tags, original_topics = list(r.public.tags), list(r.public.topic_links)
    source_tag = uuid.uuid4()
    r.labels.return_value = ({"angular": source_tag}, 1)
    extracted = page(tags=["Angular", "angular", "Ａｎｇｕｌａｒ"])
    result = apply(r, extracted)
    saved = RefreshDraft.model_validate(r.owner.payload)
    assert result == {"outcome": "enriched", "changed_fields": ["author", "published_at", "tags"]}
    assert saved.fields.title == before["title"] and saved.fields.summary == before["summary"]
    assert (
        saved.fields.author == extracted.author
        and saved.fields.published_at == extracted.published_at
    )
    assert saved.content.text == extracted.text and saved.content.method == "html"
    assert (
        saved.content.url == r.url + "?redirected=1" and saved.content.retrieved_at == support.NOW
    )
    assert saved.content.content_hash == hashlib.sha256(extracted.text.encode()).hexdigest()
    assert saved.analysis is None and r.owner.version == 2 and r.owner.status == "active"
    assert saved.tags == [RefreshTag(tag_id=source_tag, origin="source")]
    assert r.owner.source_hash == snapshot_hash(processing.candidate_snapshot(r.public, saved))
    assert r.job.result == {**extracted.evidence, "input_url": r.url}
    assert r.job.http_status == 200 and r.job.finished_at == support.NOW and r.job.error is None
    assert r.job.lease_token is None and r.job.lease_until is None
    assert (
        public_values(r.public) == before
        and r.public.tags == original_tags
        and r.public.topic_links == original_topics
    )
    r.read.refresh.assert_called_once_with(r.owner, ["payload"])
    assert list(r.labels.call_args.args[1]) == ["Angular"]
    assert (
        r.merges[0] is not r.public
        and r.merges[0].classification_provenance == r.public.classification_provenance
    )
    r.resume.assert_called_once_with(r.factory, r.owner.id)
    r.write.add.assert_not_called()


@pytest.mark.parametrize("changed", [False, True])
def test_existing_source_hash_preserves_private_analysis_until_actual_extraction_inputs_change(
    extraction, changed
):
    r = extraction
    extracted = page()
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.content = RefreshContent(
        text=extracted.text,
        content_hash=hashlib.sha256(extracted.text.encode()).hexdigest(),
        url=r.url,
        method="html",
        retrieved_at=support.NOW,
    )
    # Publisher metadata remains authoritative; fill these optional fields first
    # so an identical extraction has no metadata delta either.
    draft.fields.author, draft.fields.published_at = extracted.author, extracted.published_at
    r.owner.payload = draft.model_dump(mode="json")
    r.owner.source_hash = snapshot_hash(processing.candidate_snapshot(r.public, draft))
    previous = deepcopy(r.owner.payload["analysis"])
    if changed:
        extracted = replace(
            extracted, text="Different private extraction contains new routing evidence."
        )
    assert apply(r, extracted)["outcome"] == "enriched"
    assert r.owner.payload["analysis"] == (None if changed else previous)
    assert r.owner.analysis_job_id == r.inputs.facts["analysis"].id


@pytest.mark.parametrize("origin", ["ai", "manual"])
def test_enrichment_keeps_contextual_language_and_operator_assignments_in_private_inputs(
    extraction, origin
):
    r = extraction
    r.public.classification_provenance = {**r.public.classification_provenance, "origin": origin}
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.fields.metadata_source_type = "aggregator"
    draft.fields.language, draft.fields.content_type = "ja", "news"
    original_topics = draft.topics.copy()
    r.owner.payload = draft.model_dump(mode="json")
    tag = uuid.uuid4()
    r.labels.return_value = ({"angular": tag}, 1)
    before = public_values(r.public)
    assert apply(r, page(language="fr", tags=["Angular"]))["outcome"] == "enriched"
    saved = RefreshDraft.model_validate(r.owner.payload)
    assert saved.fields.language == "ja" and saved.fields.content_type == "news"
    assert saved.topics == original_topics and public_values(r.public) == before
    if origin == "manual":
        r.labels.assert_not_called()
        assert saved.tags == draft.tags
    else:
        assert saved.tags == [RefreshTag(tag_id=tag, origin="source")]


@pytest.mark.parametrize("state", ["not_found", "image", "source_tag"])
def test_no_page_text_retains_prior_private_body_and_reports_only_actual_metadata_changes(
    extraction, state
):
    r = extraction
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.content = RefreshContent(
        text="Previously extracted private article body.",
        content_hash="c" * 64,
        url=r.url,
        method="html",
        retrieved_at=support.NOW,
    )
    r.owner.payload = draft.model_dump(mode="json")
    r.owner.source_hash = snapshot_hash(processing.candidate_snapshot(r.public, draft))
    extracted = page(
        title=None,
        summary="",
        author=None,
        published_at=None,
        language=None,
        text_source=None,
        text="",
    )
    changed = []
    if state == "image":
        extracted = replace(extracted, image_url="https://publisher.example/new-image.png")
        changed = ["image_url"]
    elif state == "source_tag":
        extracted = replace(extracted, tags=["Angular"])
        r.labels.return_value = ({"angular": uuid.uuid4()}, 1)
        changed = ["tags"]
    assert apply(r, extracted) == {
        "outcome": "metadata_only" if changed else "not_found",
        "changed_fields": changed,
    }
    assert RefreshDraft.model_validate(r.owner.payload).content == draft.content
    assert r.owner.payload["analysis"] == draft.model_dump(mode="json")["analysis"]


@pytest.mark.parametrize("matching", [False, True])
def test_image_receipt_survives_only_when_it_matches_the_retained_private_image(
    extraction, matching
):
    r = extraction
    r.owner.image_url = "https://publisher.example/current.png"
    receipt = {
        "source_url": r.owner.image_url if matching else "https://publisher.example/old.png",
        "hash": "a" * 64,
        "original_key": "private/original.png",
        "version": "v1",
        "variants": [],
    }
    r.owner.managed_image = deepcopy(receipt)
    result = apply(r, page(image_url="https://publisher.example/ignored-replacement.png"))
    assert "image_url" not in result["changed_fields"]
    assert r.owner.image_url == "https://publisher.example/current.png"
    assert r.owner.managed_image == (receipt if matching else {})


@pytest.mark.parametrize(
    "failure",
    [
        "lease",
        "foreign_article",
        "foreign_owner",
        "source",
        "article",
        "owner",
        "canonical_url",
        "selection",
        "revision",
        "closed",
    ],
)
def test_apply_refuses_lost_lease_or_authority_without_touching_the_approved_article(
    extraction, failure
):
    r = extraction
    if failure == "lease":
        r.lease.return_value = None
    elif failure == "foreign_article":
        r.job.article_id = uuid.uuid4()
    elif failure == "foreign_owner":
        r.job.refresh_id = uuid.uuid4()
    elif failure == "source":
        r.authority.return_value = []
    elif failure == "article":
        r.write.scalar.return_value = None
    elif failure == "owner":
        r.current.return_value = None
    elif failure == "canonical_url":
        r.public.canonical_url += "/changed"
    elif failure == "selection":
        r.owner.enrichment_job_id = uuid.uuid4()
    elif failure == "revision":
        r.public.editorial_revision += 1
    else:
        # The initial read saw an active owner; its locked current lookup sees a
        # terminal replacement, without replaying a stale private result.
        terminal = SimpleNamespace(**r.owner.__dict__)
        terminal.status = "cancelled"
        r.current.return_value = terminal
    before, payload = public_values(r.public), deepcopy(r.owner.payload)
    result = apply(r)
    expected = (
        "lease_lost"
        if failure == "lease"
        else "unapproved"
        if failure == "source"
        else "superseded"
    )
    assert result == {"outcome": expected, "changed_fields": []}
    assert public_values(r.public) == before and r.owner.payload == payload and r.owner.version == 1
    assert r.job.status == (
        "running" if failure in {"lease", "foreign_article", "foreign_owner"} else "succeeded"
    )
    r.resume.assert_not_called()


@pytest.mark.parametrize(
    "status", ["cancelled", "promoted", "superseded", "pruned", "missing_owner", "missing_article"]
)
@pytest.mark.parametrize("lease", ["owned", "lost", "foreign"])
def test_late_closed_or_pruned_owner_never_loads_private_payload_and_retires_only_its_lease(
    extraction, status, lease
):
    r = extraction

    class ClosedOwner:
        id = r.owner.id
        version = r.owner.version
        payload_pruned_at = support.NOW if status == "pruned" else None

        @property
        def payload(self):
            raise AssertionError("Closed owner payload must never be read")

    closed = ClosedOwner()
    closed.status = "active" if status == "pruned" else status
    r.read.get.side_effect = lambda model, identifier, **_: (
        (None if status == "missing_owner" else closed)
        if model is ArticleRefresh
        else (None if status == "missing_article" else r.public)
    )
    if lease == "lost":
        r.lease.return_value = None
    elif lease == "foreign":
        r.job.refresh_id = uuid.uuid4()
    sequence = []

    def source(*_, **__):
        assert r.read.close.called
        sequence.append("source")
        return []

    def article(*_):
        sequence.append("article")
        return r.public

    def owned(*_):
        sequence.append("job")
        return None if lease == "lost" else r.job

    r.authority.side_effect, r.write.scalar.side_effect, r.lease.side_effect = (
        source,
        article,
        owned,
    )
    before = public_values(r.public)
    assert apply(r) == {
        "outcome": "lease_lost" if lease == "lost" else "superseded",
        "changed_fields": [],
    }
    assert sequence == ["source", "article", "job"] and public_values(r.public) == before
    assert r.job.status == ("succeeded" if lease == "owned" else "running")
    r.read.refresh.assert_not_called()
    r.labels.assert_not_called()
    assert r.merges == []
    r.resume.assert_not_called()


@pytest.mark.parametrize("races", [0, 1, 2, 3])
def test_enrichment_reprepares_current_private_version_for_bounded_cas_retries(extraction, races):
    r = extraction
    before = public_values(r.public)
    attempts = []

    def current(*_, **__):
        attempts.append(True)
        if len(attempts) <= races:
            saved = deepcopy(r.owner.payload)
            saved["fields"]["author"] = "Newer private author"
            r.owner.payload = saved
            r.owner.version += 1
        return r.owner

    r.current.side_effect = current
    if races == 3:
        with pytest.raises(analysis_tasks._ApplicationInputsChanged):
            apply(r)
        assert r.job.status == "running"
        r.resume.assert_not_called()
    else:
        assert apply(r)["outcome"] == "enriched"
        assert len(attempts) == races + 1
        assert r.owner.version == races + 2
        if races:
            assert r.owner.payload["fields"]["author"] == "Newer private author"
        r.resume.assert_called_once()
    assert len(attempts) == min(races + 1, 3)
    assert public_values(r.public) == before and not r.factory.writing


def test_source_label_budget_is_checked_before_loading_any_private_subject(extraction):
    r = extraction
    with pytest.raises(ValueError, match="too many source labels"):
        apply(r, page(tags=[f"Tag {index}" for index in range(201)]))
    r.read.get.assert_not_called()
    r.resume.assert_not_called()


@pytest.mark.parametrize("field", ["title", "author", "text"])
def test_oversized_extraction_is_rejected_before_admission_locks_or_private_output_writes(
    extraction, field
):
    r = extraction
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.fields.metadata_source_type = "aggregator"
    r.owner.payload = draft.model_dump(mode="json")
    before = deepcopy(r.owner.payload)
    length = {"title": 501, "author": 201, "text": 60_001}[field]
    with pytest.raises(ValueError):
        apply(r, replace(page(), **{field: "a" * length}))
    assert r.owner.payload == before and r.owner.version == 1
    r.authority.assert_not_called()
    r.labels.assert_not_called()
    r.resume.assert_not_called()


def test_new_source_labels_cannot_exceed_combined_private_assignment_budget(extraction):
    r = extraction
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.tags = [RefreshTag(tag_id=uuid.uuid4(), origin="manual") for _ in range(200)]
    r.owner.payload = draft.model_dump(mode="json")
    r.labels.return_value = ({"new": uuid.uuid4()}, 1)
    original = deepcopy(r.owner.payload)
    with pytest.raises(ValueError, match="too many source labels"):
        apply(r, page(tags=["New tag"]))
    assert r.owner.payload == original and r.owner.version == 1
    r.resume.assert_not_called()


def test_existing_manual_label_keeps_its_origin_when_source_reports_the_same_identity(extraction):
    r = extraction
    identifier = uuid.uuid4()
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.tags = [RefreshTag(tag_id=identifier, origin="manual")]
    r.owner.payload = draft.model_dump(mode="json")
    r.labels.return_value = ({"angular": identifier}, 0)
    result = apply(r, page(tags=["Angular"]))
    assert "tags" not in result["changed_fields"]
    assert RefreshDraft.model_validate(r.owner.payload).tags == draft.tags


def test_source_labels_cannot_push_an_individually_valid_private_payload_over_utf8_budget(
    extraction,
):
    r = extraction
    draft = RefreshDraft.model_validate(r.owner.payload)
    draft.analysis = None
    draft.fields.ai_summary = "🐍" * 6000
    draft.content = RefreshContent(
        text="🐍" * 60_000,
        content_hash="c" * 64,
        url=r.url,
        method="html",
        retrieved_at=support.NOW,
    )
    draft.topics = [
        RefreshTopic(
            topic_id=uuid.uuid4(),
            role="primary" if index == 0 else "supporting",
            relevance=0.9,
            evidence="🐍" * 2000,
            origin="manual",
        )
        for index in range(70)
    ]
    # Fit real UTF-8 payload just below the bound, leaving too little room for
    # another bounded batch of source labels. No model validator is bypassed.
    low, high = 0, 60_000
    while low < high:
        middle = (low + high + 1) // 2
        draft.fields.summary = "🐍" * middle
        size = len(json.dumps(draft.model_dump(mode="json"), ensure_ascii=False).encode())
        if size <= 1_044_000:
            low = middle
        else:
            high = middle - 1
    draft.fields.summary = "🐍" * low
    draft = RefreshDraft.model_validate(draft.model_dump())
    r.owner.payload = draft.model_dump(mode="json")
    r.owner.source_hash = snapshot_hash(processing.candidate_snapshot(r.public, draft))
    names = [f"Bounded tag {index}" for index in range(120)]
    r.labels.return_value = ({name.casefold(): uuid.uuid4() for name in names}, 120)
    before = deepcopy(r.owner.payload)
    with pytest.raises(ValueError, match="bounded private payload"):
        apply(
            r,
            page(
                title=None,
                summary="",
                author=None,
                published_at=None,
                language=None,
                text_source=None,
                text="",
                tags=names,
            ),
        )
    assert r.owner.payload == before and r.owner.version == 1
    r.resume.assert_not_called()
