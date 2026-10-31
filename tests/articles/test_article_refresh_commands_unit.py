"""Maintenance commands bind one approved base, owner and immutable replacement."""

import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_jobs, article_refresh_publication
from devfeed_core import article_refresh as commands
from devfeed_core.article_refresh import approved_digest
from devfeed_core.config import get_settings
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_WIDTHS, IMAGE_VERSION
from devfeed_core.models import Article, ArticleContent, ArticleImageJob
from devfeed_core.services import OperationConflict
from sqlalchemy.orm import Session

from tests.support.article_maintenance_unit import NOW, WorkerSessions
from tests.support.article_maintenance_unit import prepared as prepared


@pytest.fixture
def runtime(prepared, monkeypatch):
    public, owner, inputs, base = prepared
    read, write = Mock(spec=Session), Mock(spec=Session)
    write.scalar.return_value = None
    authority = Mock(return_value=[public.origins[0].source_id])
    monkeypatch.setattr(article_jobs, "approved_sources", authority)
    monkeypatch.setattr(commands, "approved_digest", lambda *_args: base.base_hash)
    monkeypatch.setattr(commands, "utcnow", lambda: NOW)
    monkeypatch.setattr(get_settings(), "published_article_refresh_enabled", True)
    monkeypatch.setattr(get_settings(), "image_storage_enabled", False)
    return SimpleNamespace(
        public=public,
        owner=owner,
        inputs=inputs,
        base=base,
        read=read,
        write=write,
        authority=authority,
        factory=WorkerSessions(read, write),
    )


def test_new_owner_copies_only_the_prepared_allowlisted_projection(runtime):
    r = runtime
    actor = {"subject": "editor"}
    result = commands.create_refresh(r.write, r.public, r.base, "analysis", actor)
    assert result.id == r.base.refresh_id and result.article_id == r.public.id
    assert result.base_revision == r.public.editorial_revision
    assert result.base_hash == r.base.base_hash and result.source_hash == r.base.source_hash
    assert result.payload == r.base.draft.model_dump(mode="json")
    assert result.analysis_job_id == r.inputs.facts["analysis"].id
    assert result.requested_by == actor and result.target == "analysis"
    assert r.public.publication_status == "published"
    r.write.add.assert_called_once_with(result)
    r.write.flush.assert_called_once()


@pytest.mark.parametrize("changed", ["id", "revision", "publication", "approval", "hash"])
def test_changed_approved_base_cannot_admit_a_private_owner(runtime, monkeypatch, changed):
    r = runtime
    base = r.base
    if changed == "id":
        base = replace(base, article_id=uuid.uuid4())
    elif changed == "revision":
        r.public.editorial_revision += 1
    elif changed == "publication":
        r.public.publication_status = "unpublished"
    elif changed == "approval":
        r.public.review_status = "rejected"
    else:
        monkeypatch.setattr(commands, "approved_digest", lambda *_: "changed")
    with pytest.raises(OperationConflict, match="approved publication changed"):
        commands.create_refresh(r.write, r.public, base, "analysis", {})
    r.write.add.assert_not_called()


def test_disabled_gate_has_no_legacy_withdrawal_or_candidate_write(runtime, monkeypatch):
    monkeypatch.setattr(get_settings(), "published_article_refresh_enabled", False)
    with pytest.raises(OperationConflict, match="disabled"):
        commands.create_refresh(runtime.write, runtime.public, runtime.base, "analysis", {})
    assert runtime.public.publication_status == "published"
    runtime.write.scalar.assert_not_called()
    runtime.write.add.assert_not_called()


@pytest.mark.parametrize("changed", [None, "target", "hash"])
def test_active_owner_is_reused_only_for_the_same_approved_base_and_primary_target(
    runtime, changed
):
    r = runtime
    r.write.scalar.return_value = r.owner
    if changed == "target":
        r.owner.target = "enrichment"
    elif changed == "hash":
        r.owner.base_hash = "changed"
    if changed:
        with pytest.raises(OperationConflict, match="Another maintenance candidate"):
            commands.create_refresh(r.write, r.public, r.base, "analysis", {})
    else:
        assert commands.create_refresh(r.write, r.public, r.base, "analysis", {}) is r.owner
    r.write.add.assert_not_called()


@pytest.mark.parametrize("with_content", [False, True])
def test_prepared_base_retains_bounded_source_inputs_and_selected_artifact_identities(
    runtime, with_content
):
    r = runtime
    content = ArticleContent(
        article_id=r.public.id,
        text="Angular source body",
        content_hash="a" * 64,
        url=r.public.canonical_url,
        method="page",
        retrieved_at=NOW,
    )
    r.read.get.return_value = content if with_content else None
    r.read.scalars.side_effect = [r.public.topic_links, []]
    for link in r.public.topic_links:
        link.origin, link.evidence = "manual", "Approved Angular assignment"
    result = commands.prepare_refresh_base(r.read, r.public, r.inputs.facts)
    assert result.refresh_id != r.public.id
    assert result.draft.fields.title == r.public.title
    assert bool(result.draft.content) == with_content
    assert result.jobs["analysis"] == r.inputs.facts["analysis"].id
    assert result.base_hash == r.base.base_hash
    assert r.public.publication_status == "published"


@pytest.mark.parametrize(
    "changed",
    [
        "title",
        "slug",
        "image",
        "receipt",
        "provenance",
        "sponsors",
        "topics",
        "tags",
        "origins",
        "content_hash",
    ],
)
def test_approved_revision_digest_fences_metadata_associations_and_receipts_without_body_reads(
    runtime, changed
):
    r = runtime
    topic_rows = [(uuid.uuid4(), "primary", 1, "Approved evidence", "manual")]
    tag_rows, origin_ids, content_hash = [(uuid.uuid4(), "manual")], [uuid.uuid4()], "a" * 64

    def digest():
        r.read.execute.side_effect = [
            SimpleNamespace(tuples=lambda: list(topic_rows)),
            SimpleNamespace(tuples=lambda: list(tag_rows)),
        ]
        r.read.scalars.return_value = origin_ids
        r.read.scalar.return_value = content_hash
        return approved_digest(r.read, r.public)

    before = digest()
    assert digest() == before
    if changed == "title":
        r.public.title += " changed"
    elif changed == "slug":
        r.public.slug += "-changed"
    elif changed == "image":
        r.public.image_url = "https://publisher.example/new.png"
    elif changed == "receipt":
        r.public.managed_image = {"hash": "different immutable image"}
    elif changed == "provenance":
        r.public.classification_provenance = {"origin": "manual"}
    elif changed == "sponsors":
        r.public.sponsor_names = ["New sponsor"]
    elif changed == "topics":
        topic_rows = [(topic_rows[0][0], "supporting", 1, "Approved evidence", "manual")]
    elif changed == "tags":
        tag_rows = [(uuid.uuid4(), "source")]
    elif changed == "origins":
        origin_ids = [uuid.uuid4()]
    else:
        content_hash = "b" * 64
    assert digest() != before
    assert "article_contents.text" not in str(r.read.scalar.call_args.args[0])


@pytest.mark.parametrize(
    "changed", [None, "missing", "terminal", "revision", "publication", "approval", "source"]
)
@pytest.mark.parametrize("lock", [False, True])
def test_owner_lookup_rechecks_current_public_authority_without_reading_payload(
    runtime, changed, lock
):
    r = runtime
    r.write.scalar.return_value = None if changed == "missing" else r.owner
    if changed == "terminal":
        r.owner.status = "cancelled"
    elif changed == "revision":
        r.public.editorial_revision += 1
    elif changed == "publication":
        r.public.publication_status = "unpublished"
    elif changed == "approval":
        r.public.review_status = "rejected"
    elif changed == "source":
        r.authority.return_value = []
    result = commands.refresh_for_article(r.write, r.public, r.owner.id, lock=lock)
    assert result is (r.owner if changed is None else None)
    sql = str(r.write.scalar.call_args.args[0])
    assert ("FOR UPDATE" in sql) == lock
    assert "article_refreshes.payload," not in sql


def receipt(url, *, version="v2", layout="cards-v1", widths=ARTICLE_THUMBNAIL_WIDTHS):
    return {
        "source_url": url,
        "hash": "stored-hash",
        "original_key": "original/key",
        "version": IMAGE_VERSION,
        "variants": [{"key": f"thumb/{w}", "width": w} for w in widths],
        "source_width": 1200,
        "thumbnail_version": version,
        "thumbnail_layout": layout,
    }


def image_job(r):
    r.owner.target = "images"
    r.owner.image_url = "https://publisher.example/replacement.png"
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=r.public.id,
        refresh_id=r.owner.id,
        operation="discover",
        status="succeeded",
        outcome="found",
    )
    r.owner.discovery_job_id = job.id
    return job


@pytest.mark.parametrize(
    "changed", [None, "missing", "article", "owner", "slot", "running", "outcome"]
)
def test_image_completion_requires_the_exact_selected_owner_artifact(runtime, changed):
    r = runtime
    job = image_job(r)
    if changed == "missing":
        job = None
    elif changed == "article":
        job.article_id = uuid.uuid4()
    elif changed == "owner":
        job.refresh_id = uuid.uuid4()
    elif changed == "slot":
        r.owner.discovery_job_id = uuid.uuid4()
    elif changed == "running":
        job.status = "running"
    elif changed == "outcome":
        job.outcome = "already_present"
    assert commands.completed_refresh_image(r.owner, job) == (changed is None)


@pytest.mark.parametrize(
    "changed", [None, "version", "source", "job_url", "width_missing", "unknown_layout", "legacy"]
)
def test_image_storage_completion_requires_every_immutable_manifest_variant(
    runtime, monkeypatch, changed
):
    r = runtime
    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    job = image_job(r)
    job.operation, job.image_url = "store", r.owner.image_url
    r.owner.storage_job_id = job.id
    asset = receipt(r.owner.image_url)
    if changed == "version":
        asset["version"] = "future"
    elif changed == "source":
        asset["source_url"] = "https://other.example/image"
    elif changed == "job_url":
        job.image_url = "https://other.example/image"
    elif changed == "width_missing":
        asset["source_width"] = None
    elif changed == "unknown_layout":
        asset["thumbnail_version"] = "future"
    elif changed == "legacy":
        asset = receipt(r.owner.image_url, version="v1", layout=None, widths=(320, 640, 960))
    r.owner.managed_image = commands.validate_refresh_image(asset)
    assert commands.completed_refresh_image(r.owner, job) == (changed in {None, "legacy"})


def test_image_promotion_changes_only_completed_image_and_audit(runtime):
    r = runtime
    job = image_job(r)
    r.read.scalar.return_value, r.read.get.return_value = r.owner, job
    r.write.scalar.side_effect = [r.public, r.owner]
    before = (
        r.public.slug,
        r.public.published_to_feed_at,
        r.public.editorial_revision,
        r.public.ai_summary,
    )
    assert commands.try_promote_refresh(r.factory, r.owner.id) == "promoted"
    assert r.public.image_url == r.owner.image_url and r.owner.status == "promoted"
    assert (
        r.public.slug,
        r.public.published_to_feed_at,
        r.public.editorial_revision,
        r.public.ai_summary,
    ) == before
    assert r.write.add.call_args.args[0].action == "refresh"


@pytest.mark.parametrize(
    "changed", ["source", "missing", "publication", "review", "revision", "hash"]
)
def test_image_promotion_never_restores_lost_public_or_source_authority(
    runtime, monkeypatch, changed
):
    r = runtime
    job = image_job(r)
    r.read.scalar.return_value, r.read.get.return_value = r.owner, job
    r.write.scalar.side_effect = [None if changed == "missing" else r.public, r.owner]
    if changed == "source":
        r.authority.return_value = []
    elif changed == "publication":
        r.public.publication_status = "unpublished"
    elif changed == "review":
        r.public.review_status = "rejected"
    elif changed == "revision":
        r.public.editorial_revision += 1
    elif changed == "hash":
        monkeypatch.setattr(commands, "approved_digest", lambda *_: "changed")
    assert commands.try_promote_refresh(r.factory, r.owner.id) == "superseded"
    assert r.public.image_url is None
    r.write.add.assert_not_called()


@pytest.mark.parametrize(
    "boundary", ["missing", "terminal", "incomplete", "write_missing", "write_terminal", "version"]
)
def test_late_image_promotion_rechecks_owner_state_and_version(runtime, boundary):
    r = runtime
    job = image_job(r)
    r.read.scalar.return_value, r.read.get.return_value = r.owner, job
    r.write.scalar.side_effect = [r.public, r.owner]
    if boundary == "missing":
        r.read.scalar.return_value = None
    elif boundary == "terminal":
        r.owner.status = "cancelled"
    elif boundary == "incomplete":
        job.status = "running"
    elif boundary == "write_missing":
        r.write.scalar.side_effect = [r.public, None]
    elif boundary in {"write_terminal", "version"}:

        def read_locked(statement):
            if statement.column_descriptions[0]["entity"] is Article:
                return r.public
            if boundary == "version":
                r.owner.version += 1
            else:
                r.owner.status = "cancelled"
            return r.owner

        r.write.scalar.side_effect = read_locked
    expected = (
        "missing"
        if boundary in {"missing", "write_missing"}
        else "cancelled"
        if boundary in {"terminal", "write_terminal"}
        else "pending"
    )
    assert commands.try_promote_refresh(r.factory, r.owner.id) == expected
    assert r.public.image_url is None


def test_nonimage_promotion_releases_read_session_before_publication_delegate(runtime, monkeypatch):
    r = runtime
    r.read.scalar.return_value = r.owner

    def promote(factory, identifier):
        r.read.close.assert_called_once()
        assert factory is r.factory and identifier == r.owner.id
        return "review"

    monkeypatch.setattr(article_refresh_publication, "promote_processed_refresh", promote)
    assert commands.try_promote_refresh(r.factory, r.owner.id) == "review"


@pytest.mark.parametrize("note", [None, "", " ", "x" * 1001])
def test_discard_requires_a_bounded_nonempty_reason_before_locks(runtime, note):
    with pytest.raises(OperationConflict, match="bounded reason"):
        commands.review_refresh(
            runtime.write,
            runtime.public.id,
            runtime.owner.id,
            1,
            action="reject",
            actor={},
            note=note,
        )
    runtime.write.scalar.assert_not_called()


@pytest.mark.parametrize(
    "changed", [None, "article_missing", "owner_missing", "version", "terminal"]
)
def test_discard_is_version_bound_and_never_changes_publication(runtime, changed):
    r = runtime
    r.owner.approved_version = r.owner.version
    r.write.scalar.side_effect = [
        None if changed == "article_missing" else r.public,
        None if changed == "owner_missing" else r.owner,
    ]
    expected = r.owner.version
    if changed == "version":
        expected += 1
    elif changed == "terminal":
        r.owner.status = "cancelled"
    if changed:
        with pytest.raises(OperationConflict):
            commands.review_refresh(
                r.write,
                r.public.id,
                r.owner.id,
                expected,
                action="reject",
                actor={"name": "Editor"},
                note="Discard replacement",
            )
    else:
        result = commands.review_refresh(
            r.write,
            r.public.id,
            r.owner.id,
            expected,
            action="reject",
            actor={"name": "Editor"},
            note=" Discard replacement ",
        )
        assert result.status == "cancelled" and result.version == expected + 1
        assert result.approved_version is None and result.review_note == "Discard replacement"
        assert r.write.add.call_args.args[0].action == "refresh-discard"
    assert r.public.publication_status == "published"


def test_approve_review_uses_the_shared_atomic_publication_command(runtime, monkeypatch):
    command = Mock(return_value=runtime.owner)
    monkeypatch.setattr(article_refresh_publication, "approve_refresh", command)
    assert (
        commands.review_refresh(
            runtime.write,
            runtime.public.id,
            runtime.owner.id,
            7,
            action="approve",
            actor={"subject": "editor"},
            note=None,
        )
        is runtime.owner
    )
    command.assert_called_once_with(
        runtime.write, runtime.public.id, runtime.owner.id, 7, {"subject": "editor"}, None
    )
