"""Taxonomy identity maintenance preserves correction and automatic link authority."""

import copy
import uuid
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import tag_topic_discovery as discovery
from devfeed_core import topic_branding as branding
from devfeed_core import topic_corrections as corrections
from devfeed_core import topic_proposals, topics
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Tag, Topic, TopicRelation
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_auto_approval import ACTOR as AUTOMATIC
from devfeed_core.topic_proposals import snapshot, topic_draft
from devfeed_core.topics import TopicWrite
from sqlalchemy.orm import Session

from tests.support.source_readiness import NOW as IDENTITY_NOW
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import ACTOR, NOW, proposal, topic


@pytest.fixture
def correction(monkeypatch):
    current = topic(
        kind="library", description="A JavaScript UI library.", updated_at=NOW - timedelta(hours=1)
    )
    previous = proposal(
        current,
        status="approved",
        reviewed_at=NOW - timedelta(hours=1),
        reviewed_by=AUTOMATIC,
        applied=snapshot(current),
    )
    v = SimpleNamespace(current=current, previous=previous, session=Mock(), added=[])
    v.session.scalar.side_effect = [current, previous, None]
    v.session.add.side_effect = v.added.append
    v.session.flush.side_effect = lambda: setattr(v.added[-1], "id", uuid.uuid4())
    v.review = Mock(return_value={"id": "approved-correction"})
    monkeypatch.setattr(corrections, "review_proposal", v.review)
    monkeypatch.setattr(corrections, "lock_topics", Mock())
    v.body = TopicWrite.model_validate(
        {
            **topic_draft(current).model_dump(),
            "description": "A library for web and native user interfaces.",
        }
    )
    return v


def correct(v, **changes):
    options = {
        "expected_hash": snapshot_hash(snapshot(v.current)),
        "body": v.body,
        "note": "Reviewed the official project identity and corrected its scope.",
        "actor": ACTOR,
    }
    options.update(changes)
    return corrections.correct_automatic_topic(v.session, v.current.id, **options)


@pytest.mark.parametrize("note", [" ", "x" * 1001])
def test_explicit_correction_requires_a_bounded_operator_reason(correction, note):
    with pytest.raises(ValueError, match="bounded correction reason"):
        correct(correction, note=note)
    assert correction.session.mock_calls == []


@pytest.mark.parametrize(
    "guard",
    [
        "missing",
        "stale",
        "no-approval",
        "human-approval",
        "changed-after-approval",
        "name",
        "slug",
        "pending-review",
    ],
)
def test_correction_requires_the_same_unchanged_automatically_approved_identity(correction, guard):
    v = correction
    if guard == "missing":
        v.session.scalar.side_effect = [None]
    elif guard == "stale":
        old_hash = snapshot_hash(snapshot(v.current))
        v.current.description = "A newer operator edit."
    elif guard == "no-approval":
        v.session.scalar.side_effect = [v.current, None]
    elif guard == "human-approval":
        v.previous.reviewed_by = ACTOR
    elif guard == "changed-after-approval":
        v.current.description = "A newer operator edit."
    elif guard in {"name", "slug"}:
        values = v.body.model_dump()
        values[guard] = "Another entity" if guard == "name" else "another-entity"
        v.body = TopicWrite.model_validate(values)
    else:
        pending = proposal(v.current)
        v.session.scalar.side_effect = [v.current, v.previous, pending.id]
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        correct(v, **({"expected_hash": old_hash} if guard == "stale" else {}))
    assert v.added == []
    v.review.assert_not_called()


def test_explicit_correction_adds_attributed_review_without_rewriting_automatic_approval(
    correction,
):
    v = correction
    before = snapshot(v.current)
    original = copy.deepcopy(v.previous.applied)
    reviewed = (v.previous.reviewed_by, v.previous.reviewed_at, v.previous.status)
    assert correct(v) == {"id": "approved-correction"}
    [created] = v.added
    assert created.id is not None and created.topic_id == v.current.id
    assert created.baseline == before and created.proposed == v.body.model_dump(mode="json")
    assert created.research_requested is False and created.created_by == ACTOR
    assert created.origin == "import" and created.action == "update"
    assert created.evidence[0]["previous_proposal_id"] == str(v.previous.id)
    assert created.evidence[0]["provider"] == "authorized_identity_correction"
    args = v.review.call_args.args
    assert args[:2] == (v.session, created.id) and args[-1] == ACTOR
    assert args[2].decision == "approved"
    assert args[2].expected_input_hash == snapshot_hash(created.proposed)
    assert args[2].topic.model_dump(mode="json") == created.proposed
    assert v.previous.applied == original
    assert (v.previous.reviewed_by, v.previous.reviewed_at, v.previous.status) == reviewed


@pytest.mark.parametrize("disabled", ["ai", "branding"])
def test_branding_scheduler_respects_independent_feature_admission(monkeypatch, disabled):
    settings = configuration(
        ai_enabled=disabled != "ai", auto_research_topic_branding=disabled != "branding"
    )
    monkeypatch.setattr(branding, "get_settings", lambda: settings)
    factory = Mock()
    assert branding.schedule_topic_branding(factory) == 0
    factory.begin.assert_not_called()


@pytest.mark.parametrize("state", ["lock-denied", "full", "one-slot", "two-slots", "batch-cap"])
def test_branding_builds_bounded_update_proposals_and_never_approves_the_topic(monkeypatch, state):
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    busy = 2 if state == "full" else 1 if state == "one-slot" else 0
    settings = configuration(
        ai_enabled=True,
        auto_research_topic_branding=True,
        automation_batch_size=1 if state == "batch-cap" else 5,
    )
    monkeypatch.setattr(branding, "get_settings", lambda: settings)
    monkeypatch.setattr(branding, "lock_topics", Mock())
    session.scalar.side_effect = [state != "lock-denied", busy]
    count = 0 if state in {"lock-denied", "full"} else 2 if state == "two-slots" else 1
    rows = [topic(name, kind="library") for name in ("React", "Angular")][:count]
    session.scalars.return_value.all.return_value = rows
    original = [snapshot(row) for row in rows]
    assert branding.schedule_topic_branding(factory) == count
    added = [call.args[0] for call in session.add.call_args_list]
    assert len(added) == count * 2
    for index, current in enumerate(rows):
        proposed, job = added[index * 2 : index * 2 + 2]
        assert proposed.topic_id == current.id and proposed.baseline == original[index]
        assert proposed.source_name == branding.VERSION and proposed.action == "update"
        assert proposed.origin == "ai_analysis" and proposed.created_by == branding.ACTOR
        assert proposed.proposed == topic_draft(current).model_dump(mode="json")
        assert job.proposal_id == proposed.id and job.topic_id is None
        assert job.input_hash == snapshot_hash(proposed.proposed)
        assert job.input_snapshot == {"topic": proposed.proposed, "evidence": [], "branding": True}
        assert job.requested_by == branding.ACTOR and job.prompt_version == branding.VERSION
        assert snapshot(current) == original[index]
    if count == 0:
        session.add.assert_not_called()


@pytest.mark.parametrize("collision", ["name", "slug"])
def test_canonical_collision_cannot_be_treated_as_a_second_topic(monkeypatch, collision):
    existing = topic(kind="library")
    body = topics.TopicWrite(
        name="React" if collision == "name" else "Other library",
        slug="react-new" if collision == "name" else "react",
        kind="library",
    )
    session = Mock()
    session.scalars.return_value.all.return_value = [existing]
    monkeypatch.setattr(topics, "lock_topics", Mock())
    with pytest.raises(OperationConflict, match=f"{collision.capitalize()}.*already identifies"):
        topics.save_topic(session, body)
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert existing.name == "React" and existing.slug == "react"


@pytest.mark.parametrize("initial_status", ["active", "proposed", "rejected"])
def test_shared_alias_is_search_vocabulary_and_does_not_merge_distinct_canonical_entities(
    monkeypatch,
    initial_status,
):
    other = topic("Rust", aliases=["React"])
    body = topics.TopicWrite(name="React", slug="react", kind="library", aliases=["ReactJS"])
    session = Mock()
    session.scalars.return_value.all.return_value = [other]
    monkeypatch.setattr(topics, "lock_topics", Mock())
    saved = topics.save_topic(session, body, initial_status=initial_status)
    session.add.assert_called_once_with(saved)
    session.flush.assert_called_once()
    assert saved.status == initial_status
    assert saved.name == "React" and saved.slug == "react" and saved.aliases == ["ReactJS"]
    assert other.name == "Rust" and other.aliases == ["React"]


@pytest.mark.parametrize("present", [False, True])
def test_existing_topic_edit_requires_its_identity_and_preserves_its_lifecycle(
    monkeypatch, present
):
    current = topic(kind="library", status="rejected")
    body = topics.TopicWrite(
        name=current.name,
        slug=current.slug,
        kind="library",
        description="A JavaScript UI library.",
        aliases=["ReactJS"],
    )
    session = Mock()
    session.scalars.return_value.all.return_value = [current] if present else []
    session.get.return_value = current if present else None
    monkeypatch.setattr(topics, "lock_topics", Mock())
    if present:
        assert topics.save_topic(session, body, current.id) is current
        assert current.status == "rejected" and current.description == body.description
        assert current.aliases == ["ReactJS"]
    else:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            topics.save_topic(session, body, current.id)
        session.flush.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("missing", ["focal", "peer", None])
@pytest.mark.parametrize("existing", [False, True])
def test_graph_command_requires_both_entities_and_updates_only_the_directed_edge(missing, existing):
    focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
    body = topics.RelationWrite(
        topic_id=focal.id,
        related_topic_id=peer.id,
        relation="uses_language",
        evidence_url="https://react.dev/learn",
    )
    edge = TopicRelation(
        topic_id=focal.id,
        related_topic_id=peer.id,
        relation="uses_language",
        evidence_url="https://react.dev/",
    )
    before = copy.deepcopy(
        (focal.name, peer.name, edge.topic_id, edge.related_topic_id, edge.relation)
    )
    session = Mock()

    def loaded(model, identifier):
        if model is Topic:
            return (
                None
                if identifier == (focal.id if missing == "focal" else peer.id) and missing
                else (focal if identifier == focal.id else peer)
            )
        assert model is TopicRelation and identifier == (focal.id, peer.id, "uses_language")
        return edge if existing else None

    session.get.side_effect = loaded
    if missing:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            topics.relate_topics(session, body)
        session.add.assert_not_called()
        assert edge.evidence_url == "https://react.dev/"
    else:
        saved = topics.relate_topics(session, body)
        assert saved.evidence_url == body.evidence_url
        assert (saved.topic_id, saved.related_topic_id, saved.relation) == (
            focal.id,
            peer.id,
            "uses_language",
        )
        if existing:
            assert saved is edge
            session.add.assert_not_called()
        else:
            session.add.assert_called_once_with(saved)
    assert (focal.name, peer.name, edge.topic_id, edge.related_topic_id, edge.relation) == before


def test_self_relation_is_rejected_before_any_entity_lookup():
    identifier, session = uuid.uuid4(), Mock()
    body = topics.RelationWrite(
        topic_id=identifier, related_topic_id=identifier, relation="depends_on"
    )
    with pytest.raises(OperationConflict, match="cannot be related to itself"):
        topics.relate_topics(session, body)
    assert session.mock_calls == []


@pytest.mark.parametrize("state", ["missing", "proposed", "rejected"])
def test_metadata_enrichment_does_not_create_or_reactivate_a_missing_or_inactive_topic(state):
    current = topic(status=state) if state != "missing" else None
    session = Mock()
    session.get.return_value = current
    session.scalar.return_value = current
    with pytest.raises(RecordNotFound if state == "missing" else OperationConflict):
        topic_proposals.preview_enrichment(session, uuid.uuid4() if current is None else current.id)
    session.scalars.assert_not_called()
    session.add.assert_not_called()


def test_import_catalog_resolves_canonical_slugs_to_existing_identity_rows():
    first, second = topic(), topic("JavaScript", kind="language")
    session = Mock()
    session.scalars.return_value = [first, second]
    assert topic_proposals.catalogs(session) == {first.slug: first, second.slug: second}
    session.add.assert_not_called()


def catalog_topic(name, slug, **changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": name,
            "slug": slug,
            "kind": "technology",
            "status": "active",
            "aliases": [],
            **changes,
        }
    )


def tag(name, slug, **changes):
    matched = changes.get("topic_match_status") == "matched"
    return Tag(
        **{
            "id": uuid.uuid4(),
            "name": name,
            "slug": slug,
            "aliases": [],
            "topic_id": None,
            "auto_link_topic": True,
            "topic_match_revision": 7 if matched else 0,
            "topic_match_status": "pending",
            "topic_match_checked_at": IDENTITY_NOW - timedelta(days=1) if matched else None,
            **changes,
        }
    )


def scan(monkeypatch, tags, topics, *, enabled=True):
    settings = configuration(auto_link_tags=enabled, ai_enabled=False)
    monkeypatch.setattr(discovery, "get_settings", lambda: settings)
    monkeypatch.setattr(discovery, "utcnow", lambda: IDENTITY_NOW)
    lock = Mock()
    monkeypatch.setattr(discovery, "lock_topics", lock)
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = 8
    session.scalars.return_value.all.return_value = tags
    session.execute.return_value = [
        (current.id, discovery.record_keys(current)) for current in topics
    ]
    factory.begin.return_value = nullcontext(session)
    output = discovery.schedule_tag_topic_discovery(factory)
    return output, session, factory, lock


def counts(scanned=0, linked=0, unlinked=0, ambiguous=0):
    return {
        "tags_scanned": scanned,
        "tags_linked": linked,
        "tags_unlinked": unlinked,
        "tags_ambiguous": ambiguous,
    }


def test_disabled_automatic_identity_discovery_does_not_open_a_catalog_session(monkeypatch):
    output, session, factory, lock = scan(monkeypatch, [], [], enabled=False)
    assert output == counts()
    factory.begin.assert_not_called()
    lock.assert_not_called()
    session.scalar.assert_not_called()


def test_empty_eligible_tag_batch_does_not_read_the_topic_catalog(monkeypatch):
    output, session, factory, lock = scan(monkeypatch, [], [])
    assert output == counts()
    factory.begin.assert_called_once_with()
    lock.assert_called_once_with(session, read=True)
    session.execute.assert_not_called()


@pytest.mark.parametrize("already_linked", [False, True])
def test_multiple_alias_hits_for_one_canonical_topic_are_one_identity_not_ambiguity(
    monkeypatch, already_linked
):
    current = catalog_topic("Kubernetes", "kubernetes", aliases=["k8s", "Kubernetes clusters"])
    label = tag(
        "K8S",
        "k8s",
        aliases=["Kubernetes", "Kubernetes_clusters"],
        topic_id=current.id if already_linked else None,
        topic_match_status="matched" if already_linked else "pending",
        topic_match_checked_at=IDENTITY_NOW - timedelta(days=1) if already_linked else None,
    )
    original = copy.deepcopy(
        (label.name, label.slug, label.aliases, current.name, current.aliases, current.status)
    )
    output, session, _, _ = scan(monkeypatch, [label], [current])
    assert output == counts(scanned=1, linked=int(not already_linked))
    assert label.topic_id == current.id and label.topic_match_status == "matched"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == IDENTITY_NOW
    assert (
        label.name,
        label.slug,
        label.aliases,
        current.name,
        current.aliases,
        current.status,
    ) == original
    session.add.assert_not_called()


@pytest.mark.parametrize("previously_linked", [False, True])
def test_shared_alias_ambiguity_never_selects_an_arbitrary_topic_and_clears_old_automatic_link(
    monkeypatch, previously_linked
):
    first = catalog_topic("Alpha language", "alpha-language", aliases=["Common"])
    second = catalog_topic("Beta language", "beta-language", aliases=["Common"])
    label = tag(
        "Common",
        "common",
        topic_id=first.id if previously_linked else None,
        topic_match_status="matched" if previously_linked else "pending",
    )
    output, session, _, _ = scan(monkeypatch, [label], [first, second])
    assert output == counts(scanned=1, unlinked=int(previously_linked), ambiguous=1)
    assert label.topic_id is None and label.topic_match_status == "ambiguous"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == IDENTITY_NOW
    assert first.status == second.status == "active"
    session.add.assert_not_called()


def test_retired_topic_identity_clears_its_old_automatic_link_without_creating_a_replacement(
    monkeypatch,
):
    retired = catalog_topic("Kubernetes", "kubernetes", status="rejected")
    label = tag("Kubernetes", "kubernetes", topic_id=retired.id, topic_match_status="matched")
    # The catalog read contains no active identity for this name. The retired
    # topic still exists, so the tag's old FK is a valid persisted history.
    output, session, _, _ = scan(monkeypatch, [label], [])
    assert output == counts(scanned=1, unlinked=1)
    assert label.topic_id is None and label.topic_match_status == "unmatched"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == IDENTITY_NOW
    assert retired.status == "rejected"
    session.add.assert_not_called()


def test_each_tag_uses_its_own_identity_and_replacements_count_both_attribution_changes(
    monkeypatch,
):
    former = catalog_topic("Legacy cluster tool", "legacy-cluster-tool")
    replacement = catalog_topic("Kubernetes", "kubernetes", aliases=["k8s"])
    language = catalog_topic("Python", "python", aliases=["CPython", "Python language"])
    cluster_tag = tag("K8S", "k8s", topic_id=former.id, topic_match_status="matched")
    language_tag = tag("Ｐｙｔｈｏｎ", "python-language", aliases=["CPython"])
    unmatched_tag = tag("Unrelated publisher label", "unrelated-publisher-label")
    # Former's identity was renamed by an editor; the new active Kubernetes
    # identity now uniquely owns k8s. Both candidate rows really overlap this batch.
    output, session, _, _ = scan(
        monkeypatch, [cluster_tag, language_tag, unmatched_tag], [replacement, language]
    )
    assert output == counts(scanned=3, linked=2, unlinked=1)
    assert (cluster_tag.topic_id, language_tag.topic_id, unmatched_tag.topic_id) == (
        replacement.id,
        language.id,
        None,
    )
    assert [
        current.topic_match_status for current in [cluster_tag, language_tag, unmatched_tag]
    ] == ["matched", "matched", "unmatched"]
    assert all(
        current.topic_match_revision == 8 and current.topic_match_checked_at == IDENTITY_NOW
        for current in [cluster_tag, language_tag, unmatched_tag]
    )
    assert former.status == replacement.status == language.status == "active"
    session.add.assert_not_called()
