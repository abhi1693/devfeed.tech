"""Automatic links require exactly one active catalog identity, without inference.

The repository supplies eligible tags and active overlapping topic identities.
These units do not emulate SQL eligibility, skip-locked behavior, or transactions.
"""

import copy
import uuid
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import tag_topic_discovery as discovery
from devfeed_core.models import Tag, Topic
from sqlalchemy.orm import Session
from test_source_readiness_unit import NOW, configuration


def topic(name, slug, **changes):
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
            "topic_match_checked_at": NOW - timedelta(days=1) if matched else None,
            **changes,
        }
    )


def scan(monkeypatch, tags, topics, *, enabled=True):
    settings = configuration(auto_link_tags=enabled, ai_enabled=False)
    monkeypatch.setattr(discovery, "get_settings", lambda: settings)
    monkeypatch.setattr(discovery, "utcnow", lambda: NOW)
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
    current = topic("Kubernetes", "kubernetes", aliases=["k8s", "Kubernetes clusters"])
    label = tag(
        "K8S",
        "k8s",
        aliases=["Kubernetes", "Kubernetes_clusters"],
        topic_id=current.id if already_linked else None,
        topic_match_status="matched" if already_linked else "pending",
        topic_match_checked_at=NOW - timedelta(days=1) if already_linked else None,
    )
    original = copy.deepcopy(
        (label.name, label.slug, label.aliases, current.name, current.aliases, current.status)
    )
    output, session, _, _ = scan(monkeypatch, [label], [current])
    assert output == counts(scanned=1, linked=int(not already_linked))
    assert label.topic_id == current.id and label.topic_match_status == "matched"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == NOW
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
    first = topic("Alpha language", "alpha-language", aliases=["Common"])
    second = topic("Beta language", "beta-language", aliases=["Common"])
    label = tag(
        "Common",
        "common",
        topic_id=first.id if previously_linked else None,
        topic_match_status="matched" if previously_linked else "pending",
    )
    output, session, _, _ = scan(monkeypatch, [label], [first, second])
    assert output == counts(scanned=1, unlinked=int(previously_linked), ambiguous=1)
    assert label.topic_id is None and label.topic_match_status == "ambiguous"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == NOW
    assert first.status == second.status == "active"
    session.add.assert_not_called()


def test_retired_topic_identity_clears_its_old_automatic_link_without_creating_a_replacement(
    monkeypatch,
):
    retired = topic("Kubernetes", "kubernetes", status="rejected")
    label = tag("Kubernetes", "kubernetes", topic_id=retired.id, topic_match_status="matched")
    # The catalog read contains no active identity for this name. The retired
    # topic still exists, so the tag's old FK is a valid persisted history.
    output, session, _, _ = scan(monkeypatch, [label], [])
    assert output == counts(scanned=1, unlinked=1)
    assert label.topic_id is None and label.topic_match_status == "unmatched"
    assert label.topic_match_revision == 8 and label.topic_match_checked_at == NOW
    assert retired.status == "rejected"
    session.add.assert_not_called()


def test_each_tag_uses_its_own_identity_and_replacements_count_both_attribution_changes(
    monkeypatch,
):
    former = topic("Legacy cluster tool", "legacy-cluster-tool")
    replacement = topic("Kubernetes", "kubernetes", aliases=["k8s"])
    language = topic("Python", "python", aliases=["CPython", "Python language"])
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
        current.topic_match_revision == 8 and current.topic_match_checked_at == NOW
        for current in [cluster_tag, language_tag, unmatched_tag]
    )
    assert former.status == replacement.status == language.status == "active"
    session.add.assert_not_called()
