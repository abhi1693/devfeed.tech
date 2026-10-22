"""Editorial deletion policy over scoped rows; integration tests own SQL/rollback proof."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import tag_topic_discovery
from devfeed_core import topic_deletion as deletion
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import (
    ArticleReview,
    ArticleTopic,
    Tag,
    Topic,
    UserAccount,
    UserStackAssociation,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_proposals import snapshot
from psycopg.errors import ConnectionFailure, LockNotAvailable
from sqlalchemy.exc import OperationalError
from taxonomy_command_helpers import ACTOR, NOW, proposal, relationship_job, topic
from test_processing_retry_eligibility_unit import scenario


@pytest.fixture
def runtime(monkeypatch):
    article, facts = scenario()
    assert derive_publication_readiness(article, **facts).stage == "ready"
    article.publication_status = "published"
    article.published_to_feed_at = facts["now"] - timedelta(minutes=1)
    removed = article.topic_links[0].topic
    removed.aliases, removed.keywords, removed.facts = [], [], []
    replacement, other = topic("JavaScript"), topic("Python")
    link = article.topic_links[0]
    link.article_id, link.origin, link.evidence = article.id, "ai", "Angular routing"
    pending = proposal(removed)
    history = proposal(
        removed,
        status="approved",
        reviewed_by=ACTOR,
        reviewed_at=NOW - timedelta(hours=1),
        applied=snapshot(removed),
    )
    tag = Tag(
        id=uuid.uuid4(),
        name="Routing",
        slug="routing",
        aliases=[],
        topic_id=removed.id,
        auto_link_topic=True,
        topic_match_status="matched",
    )
    account = UserAccount(
        id=uuid.uuid4(),
        issuer="identity.example",
        subject="reader-123",
        organization_id="readers",
        profile={
            "public": False,
            "dev_card": {
                "technologies": [str(removed.id), str(other.id), str(replacement.id)],
                "theme": "dark",
            },
        },
    )
    stack = UserStackAssociation(
        id=uuid.uuid4(),
        user_id=account.id,
        topic_id=removed.id,
        section="primary",
        since_year=2020,
        position=0,
    )
    value = SimpleNamespace(
        article=article,
        removed=removed,
        replacement=replacement,
        other=other,
        link=link,
        pending=pending,
        history=history,
        tag=tag,
        account=account,
        stack=stack,
        job=relationship_job(removed, other, status="running"),
        session=MagicMock(),
        replacement_proposal=None,
        existing_link=None,
        existing_stack=None,
    )
    monkeypatch.setattr(deletion, "utcnow", lambda: NOW)
    value.lock, value.changed = Mock(), Mock()
    monkeypatch.setattr(deletion, "lock_topics", value.lock)
    monkeypatch.setattr(tag_topic_discovery, "catalog_changed", value.changed)

    def save(_session, draft, *, initial_status):
        assert initial_status == "proposed"
        value.replacement = Topic(id=uuid.uuid4(), **draft.model_dump(), status=initial_status)
        return value.replacement

    value.save = Mock(side_effect=save)
    monkeypatch.setattr(deletion, "save_topic", value.save)
    return value


def execute(value, *, replacement=True, proposal_id=None):
    # These are already-scoped repository results. No SQL predicate/cascade emulation.
    scalar_rows = [value.removed]
    if proposal_id:
        scalar_rows.append(value.replacement_proposal)
    scalar_rows.append(value.existing_stack)
    value.session.scalar.side_effect = scalar_rows
    batches = [
        [value.article],
        [value.link],
        [value.tag],
        [value.pending, value.history],
        [value.job],
        [value.stack],
        [value.account],
        [],
        [],
    ]
    value.session.scalars.side_effect = [
        SimpleNamespace(all=lambda rows=rows: rows) for rows in batches
    ]
    value.session.get.side_effect = lambda model, identifier: (
        value.replacement
        if model is Topic and identifier == value.replacement.id
        else value.existing_link
        if model is ArticleTopic
        else None
    )
    deletion.delete_topic(
        value.session,
        value.removed.id,
        ACTOR,
        replacement_id=value.replacement.id if replacement else None,
        replacement_proposal_id=proposal_id,
    )


@pytest.mark.parametrize("relink", [False, True])
def test_deletion_withdraws_the_article_and_keeps_prose_history_and_profile_settings(
    runtime, relink
):
    prose = (runtime.article.title, runtime.article.summary, runtime.article.ai_summary)
    previous_revision = runtime.article.editorial_revision
    previous_history = copy.deepcopy(
        (runtime.history.proposed, runtime.history.applied, runtime.history.reviewed_at)
    )
    execute(runtime, replacement=relink)
    assert runtime.article.publication_status == "unpublished"
    assert runtime.article.review_status == "pending"
    assert runtime.article.editorial_revision == previous_revision + 1
    assert prose == (runtime.article.title, runtime.article.summary, runtime.article.ai_summary)
    change = runtime.article.classification_provenance["topic_change"]
    assert change == {
        "removed_topic_id": str(runtime.removed.id),
        "replacement_topic_id": str(runtime.replacement.id) if relink else None,
        "actor": ACTOR,
        "changed_at": NOW.isoformat(),
    }
    assert runtime.pending.status == "rejected" and runtime.pending.reviewed_by == ACTOR
    assert runtime.pending.reviewed_at == NOW and runtime.pending.topic_id is None
    assert runtime.history.status == "approved" and runtime.history.topic_id is None
    assert previous_history == (
        runtime.history.proposed,
        runtime.history.applied,
        runtime.history.reviewed_at,
    )
    assert runtime.account.profile["public"] is False
    assert runtime.account.profile["dev_card"]["theme"] == "dark"
    technologies = runtime.account.profile["dev_card"]["technologies"]
    assert set(technologies) == {str(runtime.other.id), str(runtime.replacement.id)}
    assert len(technologies) == 2
    if relink:
        assert technologies == [str(runtime.replacement.id), str(runtime.other.id)]
    else:
        assert technologies == [str(runtime.other.id), str(runtime.replacement.id)]
    assert runtime.tag.topic_id == (runtime.replacement.id if relink else None)
    assert not runtime.tag.auto_link_topic and runtime.tag.topic_match_status == "manual"
    audits = [
        c.args[0]
        for c in runtime.session.add.call_args_list
        if isinstance(c.args[0], ArticleReview)
    ]
    assert len(audits) == 1 and audits[0].actor == ACTOR["subject"]
    assert audits[0].action == ("topic_relink" if relink else "topic_delete")
    assert audits[0].revision == previous_revision + 1
    runtime.changed.assert_called_once_with(runtime.session)


def test_deleted_topic_does_not_reopen_a_rejected_article(runtime):
    runtime.article.publication_status, runtime.article.review_status = "unpublished", "rejected"
    execute(runtime, replacement=False)
    assert runtime.article.review_status == "rejected" and runtime.article.editorial_revision == 3


@pytest.mark.parametrize(
    "existing_role,removed_role,expected_role,expected_evidence",
    [
        ("comparison", "primary", "primary", "Angular routing"),
        ("primary", "supporting", "primary", "Existing reviewed evidence"),
        ("supporting", "supporting", "supporting", "Existing reviewed evidence"),
    ],
)
def test_replacement_preserves_the_stronger_assignment_and_highest_relevance(
    runtime, existing_role, removed_role, expected_role, expected_evidence
):
    runtime.link.role, runtime.link.relevance = removed_role, 0.95
    runtime.existing_link = ArticleTopic(
        article_id=runtime.article.id,
        topic_id=runtime.replacement.id,
        role=existing_role,
        relevance=0.7,
        evidence="Existing reviewed evidence",
        origin="manual",
    )
    execute(runtime)
    assert runtime.existing_link.role == expected_role
    assert runtime.existing_link.evidence == expected_evidence
    assert runtime.existing_link.relevance == 0.95 and runtime.existing_link.origin == "manual"
    assert not any(isinstance(c.args[0], ArticleTopic) for c in runtime.session.add.call_args_list)


@pytest.mark.parametrize("existing_stack", [False, True])
def test_replacement_preserves_a_readers_existing_stack_choice_or_moves_original_attributes(
    runtime, existing_stack
):
    runtime.existing_stack = uuid.uuid4() if existing_stack else None
    execute(runtime)
    rows = [
        c.args[0]
        for c in runtime.session.add.call_args_list
        if isinstance(c.args[0], UserStackAssociation)
    ]
    if existing_stack:
        assert rows == []
    else:
        assert len(rows) == 1
        assert (
            rows[0].user_id,
            rows[0].topic_id,
            rows[0].section,
            rows[0].since_year,
            rows[0].position,
        ) == (runtime.account.id, runtime.replacement.id, "primary", 2020, 0)
    assignment = next(
        c.args[0] for c in runtime.session.add.call_args_list if isinstance(c.args[0], ArticleTopic)
    )
    assert assignment.topic_id == runtime.replacement.id and assignment.origin == "manual"
    assert assignment.evidence == runtime.link.evidence


def test_pending_replacement_is_created_as_proposed_without_approving_its_import(runtime):
    runtime.replacement_proposal = proposal()
    execute(runtime, replacement=False, proposal_id=runtime.replacement_proposal.id)
    assert runtime.save.call_args.kwargs == {"initial_status": "proposed"}
    assert runtime.replacement.status == "proposed"
    assert runtime.replacement_proposal.topic_id == runtime.replacement.id
    assert runtime.replacement_proposal.baseline == snapshot(runtime.replacement)
    assert runtime.replacement_proposal.status == "pending"
    assert (
        runtime.article.review_status == "pending"
        and runtime.article.publication_status == "unpublished"
    )


@pytest.mark.parametrize(
    "invalid,error,message",
    [
        ("two-replacements", OperationConflict, "Choose one"),
        ("missing-topic", RecordNotFound, "Topic not found"),
        ("missing-proposal", RecordNotFound, "Replacement proposal not found"),
        ("reviewed-proposal", OperationConflict, "Replacement proposal changed"),
        ("detached-update", OperationConflict, "no longer has a topic"),
        ("same-topic", OperationConflict, "different replacement"),
        ("missing-replacement", RecordNotFound, "Replacement topic not found"),
    ],
)
def test_invalid_replacement_is_refused_before_any_link_changes(runtime, invalid, error, message):
    replacement_id, proposal_id = runtime.replacement.id, None
    proposed = proposal()
    rows = [runtime.removed]
    if invalid in {"two-replacements", "missing-proposal", "reviewed-proposal", "detached-update"}:
        proposal_id = proposed.id
        if invalid != "two-replacements":
            replacement_id = None
        if invalid == "reviewed-proposal":
            proposed.status, proposed.reviewed_at, proposed.reviewed_by = "rejected", NOW, ACTOR
        if invalid == "detached-update":
            proposed.action = "update"
        rows.append(None if invalid == "missing-proposal" else proposed)
    elif invalid == "missing-topic":
        rows = [None]
    elif invalid == "same-topic":
        replacement_id = runtime.removed.id
    runtime.session.scalar.side_effect = rows
    runtime.session.get.return_value = None if invalid == "missing-replacement" else runtime.removed
    with pytest.raises(error, match=message):
        deletion.delete_topic(
            runtime.session, runtime.removed.id, ACTOR, replacement_id, proposal_id
        )
    runtime.session.scalars.assert_not_called()
    runtime.session.execute.assert_not_called()
    assert runtime.pending.status == "pending" and runtime.tag.topic_id == runtime.removed.id


@pytest.mark.parametrize("locked", [False, True])
def test_database_contention_is_translated_but_other_database_errors_propagate(monkeypatch, locked):
    error = OperationalError(
        "delete topic", {}, LockNotAvailable() if locked else ConnectionFailure()
    )
    monkeypatch.setattr(deletion, "_delete_topic", Mock(side_effect=error))
    with pytest.raises(OperationConflict if locked else OperationalError) as caught:
        deletion.delete_topic(MagicMock(), uuid.uuid4(), ACTOR)
    if locked:
        assert "being updated" in str(caught.value) and caught.value.__cause__ is error
    else:
        assert caught.value is error


@pytest.mark.parametrize("empty", [False, True])
def test_deletion_preview_maps_counts_without_altering_the_topic(empty):
    session = Mock()
    session.get.return_value = topic()
    counts = [3, 1, 2, 2, 4, 1, 1] if not empty else [0] * 7
    session.scalar.side_effect = counts
    impact = deletion.deletion_impact(session, session.get.return_value.id)
    assert list(impact.model_dump().values()) == (counts if not empty else [0] * 7)
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_missing_topic_has_no_deletion_preview():
    session = Mock()
    session.get.return_value = None
    with pytest.raises(RecordNotFound, match="Topic not found"):
        deletion.deletion_impact(session, uuid.uuid4())
    session.scalar.assert_not_called()
