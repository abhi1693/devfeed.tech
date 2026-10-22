"""Source vocabulary can request research but cannot approve a catalog identity.

Tag and identity rows are selected repository inputs. These units test policy,
normalization and new proposal contents, leaving SQL selection to integration.
"""

import copy
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import ai_content
from devfeed_core import article_automation as automation
from devfeed_core.models import Tag
from devfeed_core.topics import TopicWrite
from test_analysis_admission_contracts_unit import admission as admission
from test_source_readiness_unit import configuration


@pytest.mark.parametrize(
    "guard", ["manual", "paused", "outside-window", "no-candidate", "lost-candidate"]
)
def test_source_topic_admission_spends_no_research_when_policy_or_candidates_do_not_allow_it(
    admission,
    monkeypatch,
    guard,
):
    v = admission
    settings = configuration(
        ai_enabled=True,
        full_automation=guard != "manual",
        article_topic_proposals_enabled=guard != "paused",
        ai_content_not_before=date(2026, 1, 1) if guard == "outside-window" else None,
    )
    monkeypatch.setattr(automation, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_content, "get_settings", lambda: settings)
    lock = Mock()
    monkeypatch.setattr(automation, "lock_article_catalog", lock)
    if guard == "outside-window":
        v.article.published_at = v.facts["now"].replace(year=2025)
    v.session.scalar.side_effect = [guard != "no-candidate"]
    v.session.scalars.return_value = SimpleNamespace(all=lambda: [])
    assert automation.propose_source_topics(v.session, v.article) == 0
    v.session.add.assert_not_called()
    assert lock.call_count == int(guard == "lost-candidate")


@pytest.mark.parametrize("blocked", [False, True])
def test_source_vocabulary_preserves_existing_identity_keys_and_creates_only_pending_research(
    admission,
    monkeypatch,
    blocked,
):
    v = admission
    settings = configuration(ai_enabled=True, full_automation=True, ai_content_not_before=None)
    monkeypatch.setattr(automation, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_content, "get_settings", lambda: settings)
    monkeypatch.setattr(automation, "lock_topics", Mock())
    tags = [
        Tag(
            id=uuid.uuid4(),
            name="Angular",
            slug="angular",
            aliases=["Angular framework"],
            auto_link_topic=True,
            topic_id=None,
        ),
        Tag(
            id=uuid.uuid4(),
            name="React",
            slug="react",
            aliases=[],
            auto_link_topic=True,
            topic_id=None,
        ),
    ]
    tags.sort(key=lambda tag: tag.id)
    v.session.scalar.side_effect = [True]
    # The third read supplies an inactive catalog identity's immutable identity
    # keys; eligibility must not recreate it as a new pending proposal.
    v.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: tags),
        [],
        [["angular"]] if blocked else [],
    ]
    original = copy.deepcopy(
        (v.article.review_status, v.article.publication_status, v.article.editorial_revision)
    )
    assert automation.propose_source_topics(v.session, v.article) == (1 if blocked else 2)
    created = [call.args[0] for call in v.session.add.call_args_list]
    assert {row.slug for row in created} == ({"react"} if blocked else {"react", "angular"})
    for row in created:
        draft = TopicWrite.model_validate(row.proposed)
        assert draft.kind == "unclassified" and draft.slug == row.slug
        assert row.origin == "article_enrichment" and row.action == "create"
        assert row.research_requested is True
        assert row.created_by["subject"] == automation.SOURCE_ACTOR
        assert row.evidence[0]["article_id"] == str(v.article.id)
        assert row.evidence[0]["url"] == v.article.canonical_url
        assert row.evidence[0]["tag_id"] in {str(tag.id) for tag in tags}
    assert (
        v.article.review_status,
        v.article.publication_status,
        v.article.editorial_revision,
    ) == original
    v.session.flush.assert_called_once()


@pytest.mark.parametrize(
    "drafts,expected",
    [
        ([], False),
        ([{"name": "React", "slug": "react"}], False),
        (
            [
                {
                    "name": "Router framework",
                    "slug": "router-framework",
                    "aliases": ["Angular"],
                    "keywords": [],
                }
            ],
            True,
        ),
    ],
)
def test_pending_identity_match_uses_source_evidence_without_turning_it_into_approval(
    admission,
    monkeypatch,
    drafts,
    expected,
):
    v = admission
    monkeypatch.setattr(automation, "catalog_snapshot", lambda _session, _names, loader: loader())
    v.session.scalars.return_value = drafts
    v.session.scalars.side_effect = None
    assert automation.pending_topic_matches(v.session, v.prior.input_snapshot) is expected
    v.session.add.assert_not_called()
