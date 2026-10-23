"""Topic repair review contracts; database locks and atomic writes are integration tested."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import analysis, article_lifecycle
from devfeed_core import article_topic_corrections as repairs
from devfeed_core.inference_validation import InferenceValidationError
from devfeed_core.models import ArticleContent, ArticleReview, ArticleTag, ArticleTopic, Topic
from devfeed_core.services import OperationConflict, RecordNotFound

from tests.articles.test_processing_retry_eligibility_unit import scenario

read_state_projection = repairs._state


def fixture():
    article, facts = scenario()
    link = article.topic_links[0]
    link.article_id = article.id
    link.origin = "ai"
    link.evidence = "Angular routing"
    link.topic.aliases, link.topic.keywords = [], []
    replacement = Topic(
        id=uuid.uuid4(),
        name="Web routing",
        slug="web-routing",
        kind="concept",
        status="active",
        aliases=[],
        keywords=[],
    )
    body = repairs.TopicCorrectionIntent(
        article_id=article.id,
        remove_topic_ids=[link.topic_id],
        topics=[
            {
                "topic_id": replacement.id,
                "role": "primary",
                "relevance": 0.95,
                "evidence": "Angular routing",
            }
        ],
        actor="Human editor",
        note="Correct the inferred framework to the supported routing concept.",
    )
    return SimpleNamespace(
        article=article, facts=facts, link=link, replacement=replacement, body=body
    )


def state_from_rows(value, *, links=None, tags=None, definitions=None, content=None):
    """A scoped read result, without interpreting SQL predicates or simulating persistence."""
    repository = Mock()
    repository.scalars.side_effect = [
        SimpleNamespace(all=lambda: [value.link] if links is None else links),
        SimpleNamespace(all=lambda: [] if tags is None else tags),
        SimpleNamespace(
            all=lambda: (
                [value.link.topic, value.replacement] if definitions is None else definitions
            )
        ),
    ]
    repository.get.return_value = content
    return read_state_projection(repository, value.article, value.body)


@pytest.mark.parametrize("changed", ["source", "tag", "definition", "editorial", "assignment"])
def test_review_hash_changes_when_any_reviewed_fact_changes(changed):
    value = fixture()
    state, links, source = state_from_rows(value)
    before = analysis.snapshot_hash(state)
    options = {}
    if changed == "source":
        options["content"] = ArticleContent(
            article_id=value.article.id,
            text="New publisher evidence about routing",
            method="readability",
        )
    elif changed == "tag":
        options["tags"] = [
            ArticleTag(article_id=value.article.id, tag_id=uuid.uuid4(), origin="manual")
        ]
    elif changed == "definition":
        value.replacement.status = "inactive"
    elif changed == "editorial":
        value.article.editorial_revision += 1
    else:
        value.link.origin = "manual"
    after, _, _ = state_from_rows(value, **options)
    assert analysis.snapshot_hash(after) != before
    assert state["source_hash"] == analysis.snapshot_hash(source)
    assert links == [value.link]


@pytest.mark.parametrize("apply", [False, True])
def test_missing_article_fails_before_catalog_access(monkeypatch, apply):
    session = Mock()
    session.scalar.return_value = None
    lock = Mock()
    monkeypatch.setattr(repairs, "lock_topics", lock)
    with pytest.raises(RecordNotFound, match="Article not found"):
        repairs._article(session, uuid.uuid4(), apply=apply)
    lock.assert_not_called()


@pytest.mark.parametrize("apply", [False, True])
def test_only_applying_a_plan_requires_catalog_lock(monkeypatch, apply):
    value, session, lock = fixture(), Mock(), Mock()
    session.scalar.return_value = value.article
    monkeypatch.setattr(repairs, "lock_topics", lock)
    assert repairs._article(session, value.article.id, apply=apply) is value.article
    if apply:
        lock.assert_called_once_with(session, read=True)
    else:
        lock.assert_not_called()


@pytest.mark.parametrize(
    "violation,message",
    [
        ("not-published", "previously published"),
        ("missing-assignment", "no longer exists"),
        ("manual-removal", "Only inferred"),
        ("retained-overwrite", "retained topic"),
        ("inactive-replacement", "already be active"),
        ("unsupported-evidence", "not present"),
    ],
)
def test_repair_cannot_override_human_work_or_add_unsupported_topics(violation, message):
    value, session = fixture(), Mock()
    source = analysis.source_snapshot(value.article, None)
    links = [value.link]
    body = value.body.model_copy(deep=True)
    if violation == "not-published":
        body.restore_publication = True
    elif violation == "missing-assignment":
        body.remove_topic_ids = [uuid.uuid4()]
    elif violation == "manual-removal":
        value.link.origin = "manual"
    elif violation == "retained-overwrite":
        links.append(
            ArticleTopic(
                article_id=value.article.id,
                topic_id=value.replacement.id,
                origin="manual",
                role="supporting",
                relevance=0.8,
                evidence="Angular routing",
            )
        )
    elif violation == "unsupported-evidence":
        body.topics[0].evidence = "Quantum database deployment"
    session.scalars.return_value = (
        [] if violation == "inactive-replacement" else [value.replacement.id]
    )
    with pytest.raises(
        InferenceValidationError if violation == "unsupported-evidence" else OperationConflict,
        match=message,
    ):
        repairs._changes(session, body, value.article, links, source)
    session.add.assert_not_called()
    session.delete.assert_not_called()


def test_supported_active_replacement_and_removal_only_plan_are_valid():
    value, session = fixture(), Mock()
    session.scalars.return_value = [value.replacement.id]
    source = analysis.source_snapshot(value.article, None)
    assert repairs._changes(session, value.body, value.article, [value.link], source) == [
        value.link
    ]
    value.body.topics = []
    session.reset_mock()
    assert repairs._changes(session, value.body, value.article, [value.link], source) == [
        value.link
    ]
    session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "review,restore,expected",
    [
        ("approved", False, "pending"),
        ("rejected", False, "rejected"),
        ("approved", True, "approved"),
    ],
)
def test_preview_reports_exact_delta_and_visibility_without_writes(
    monkeypatch, review, restore, expected
):
    value, session = fixture(), Mock()
    if restore:
        assert (
            article_lifecycle.derive_publication_readiness(value.article, **value.facts).stage
            == "ready"
        )
        value.article.publication_status = "published"
        value.article.published_to_feed_at = value.facts["now"]
    value.article.review_status = review
    value.body.restore_publication = restore
    state = state_from_rows(value)
    monkeypatch.setattr(repairs, "_article", lambda *args, **kwargs: value.article)
    monkeypatch.setattr(repairs, "_state", lambda *_: state)
    session.scalars.return_value = [value.replacement.id]
    preview = repairs.preview_article_topic_correction(session, value.body)
    assert preview["correction"]["expected_revision"] == value.article.editorial_revision
    assert preview["correction"]["expected_state_hash"] == analysis.snapshot_hash(state[0])
    assert preview["removed"][0]["origin"] == "ai"
    assert preview["added"][0]["topic_id"] == str(value.replacement.id)
    assert preview["added"][0]["origin"] == "manual"
    assert preview["review_status"] == expected
    assert preview["publication_status"] == ("published" if restore else "unpublished")
    session.add.assert_not_called()
    session.delete.assert_not_called()
    session.flush.assert_not_called()


@pytest.fixture
def command(monkeypatch):
    value, session = fixture(), Mock()
    before = state_from_rows(value)
    body = repairs.ArticleTopicCorrection(
        **value.body.model_dump(),
        expected_revision=value.article.editorial_revision,
        expected_state_hash=analysis.snapshot_hash(before[0]),
    )

    def read_rows(*_):
        if read.call_count == 1:
            return value.before
        assignments = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], ArticleTopic)
        ]
        result = state_from_rows(value, links=assignments)
        value.after = result[0]
        return result

    read = Mock(side_effect=read_rows)
    monkeypatch.setattr(repairs, "_article", lambda *_args, **_kwargs: value.article)
    monkeypatch.setattr(repairs, "_state", read)
    session.scalar.return_value = None
    session.scalars.return_value = [value.replacement.id]
    decision = Mock()
    monkeypatch.setattr(repairs, "decide_article", decision)
    value.session, value.plan, value.before = session, body, before
    value.read, value.decision = read, decision
    return value


@pytest.mark.parametrize("changed", ["revision", "source-hash"])
def test_stale_review_plan_cannot_mutate_assignments(command, changed):
    if changed == "revision":
        command.article.editorial_revision += 1
    else:
        command.plan.expected_state_hash = "0" * 64
    with pytest.raises(OperationConflict, match="preview the correction again"):
        repairs.apply_article_topic_correction(command.session, command.plan)
    command.session.add.assert_not_called()
    command.session.delete.assert_not_called()
    command.session.execute.assert_not_called()


@pytest.mark.parametrize("review", ["approved", "rejected"])
def test_apply_replaces_only_selected_ai_links_and_records_auditable_delta(command, review):
    command.article.review_status = review
    command.before = state_from_rows(command)
    command.plan.expected_state_hash = analysis.snapshot_hash(command.before[0])
    prose = (command.article.ai_title, command.article.ai_summary, command.article.ai_description)
    provenance = copy.deepcopy(command.article.classification_provenance)
    result = repairs.apply_article_topic_correction(command.session, command.plan)
    assert (
        result["status"] == "applied"
        and result["editorial_revision"] == command.plan.expected_revision + 1
    )
    command.session.delete.assert_called_once_with(command.link)
    added = [call.args[0] for call in command.session.add.call_args_list]
    assignment = next(row for row in added if isinstance(row, ArticleTopic))
    assert (
        assignment.article_id == command.article.id
        and assignment.topic_id == command.replacement.id
    )
    assert assignment.origin == "manual" and assignment.evidence == "Angular routing"
    reviews = [row for row in added if isinstance(row, ArticleReview)]
    assert [row.action for row in reviews] == ["classify", "correct_topics"]
    audit = reviews[1]
    assert audit.automation["before_state_hash"] == command.plan.expected_state_hash
    assert audit.automation["after_state_hash"] == analysis.snapshot_hash(command.after)
    assert audit.automation["removed"] == result["removed"]
    assert audit.automation["added"] == result["added"]
    assert audit.automation["previous_provenance"] == provenance
    assert command.article.classification_provenance["origin"] == "manual"
    assert (
        command.article.classification_provenance["topic_correction"]["actor"] == command.plan.actor
    )
    assert command.article.review_status == ("rejected" if review == "rejected" else "pending")
    assert command.article.publication_status == "unpublished"
    assert prose == (
        command.article.ai_title,
        command.article.ai_summary,
        command.article.ai_description,
    )
    command.decision.assert_not_called()


@pytest.mark.parametrize("changed", [None, "revision", "after-state"])
def test_repeated_plan_is_idempotent_only_while_its_audited_result_is_current(command, changed):
    # This is the persisted receipt of a completed correction, at the later revision.
    command.article.editorial_revision += 1
    command.article.review_status = "pending"
    command.article.classification_provenance = {
        **command.article.classification_provenance,
        "origin": "manual",
        "topic_correction": {
            "id": analysis.snapshot_hash(command.plan.model_dump(mode="json")),
            "actor": command.plan.actor,
            "changed_at": command.facts["now"].isoformat(),
        },
    }
    replacement = ArticleTopic(
        article_id=command.article.id, **command.plan.topics[0].model_dump(), origin="manual"
    )
    command.before = state_from_rows(command, links=[replacement])
    previous = ArticleReview(
        article_id=command.article.id,
        action=repairs.ACTION,
        actor=command.plan.actor,
        note=command.plan.note,
        revision=command.article.editorial_revision,
        automation={
            "correction_id": analysis.snapshot_hash(command.plan.model_dump(mode="json")),
            "after_state_hash": analysis.snapshot_hash(command.before[0]),
        },
    )
    command.session.scalar.return_value = previous
    if changed == "revision":
        command.article.editorial_revision += 1
    elif changed == "after-state":
        previous.automation["after_state_hash"] = "0" * 64
    if changed:
        with pytest.raises(OperationConflict, match="changed after this correction"):
            repairs.apply_article_topic_correction(command.session, command.plan)
    else:
        assert repairs.apply_article_topic_correction(command.session, command.plan) == {
            "article_id": str(command.article.id),
            "status": "already_applied",
        }
    command.session.add.assert_not_called()
    command.session.delete.assert_not_called()


def test_requested_republication_still_passes_approve_then_publish_authority(command):
    assert (
        article_lifecycle.derive_publication_readiness(command.article, **command.facts).stage
        == "ready"
    )
    command.article.publication_status = "published"
    command.article.published_to_feed_at = command.facts["now"]
    command.plan.restore_publication = True
    command.before = state_from_rows(command)
    command.plan.expected_state_hash = analysis.snapshot_hash(command.before[0])
    seen = []

    def decide(session, identifier, decision):
        assert session is command.session and identifier == command.article.id
        assert decision.expected_revision == command.article.editorial_revision
        seen.append(decision.action)
        if decision.action == "approve":
            assert command.article.publication_status == "unpublished"
            command.article.review_status = "approved"
        else:
            assert command.article.review_status == "approved"
            command.article.publication_status = "published"
        command.article.editorial_revision += 1

    command.decision.side_effect = decide
    repairs.apply_article_topic_correction(command.session, command.plan)
    assert seen == ["approve", "publish"]
    assert (
        command.article.publication_status == "published"
        and command.article.review_status == "approved"
    )
    audit = command.session.add.call_args.args[0]
    assert audit.action == repairs.ACTION and audit.revision == command.article.editorial_revision
