"""Starter and saved feed reads retain issued identities over scoped repository rows.

The supplied rows are already eligible for the requested reader and filters.
These cases prove fallback, continuation and projection behavior, not SQL joins,
visibility predicates, repeatable-read isolation or cache atomicity.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import must_reads
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import UserMustRead
from devfeed_core.user_settings import FeedSettings
from devfeed_user_api import recommendations as feeds
from fastapi import HTTPException
from sqlalchemy.orm import Session
from test_admin_article_board_policy_unit import history
from test_analysis import result
from test_article_readiness_unit import processing_case as processing_case
from test_personal_feed_contracts_unit import FeedReads, recommendation, request
from test_personal_feed_contracts_unit import scenario as scenario
from test_source_readiness_unit import NOW


@pytest.fixture
def stories(processing_case):
    items = []
    for index in range(2):
        current, facts = history(processing_case, f"reader-starter-{index}")
        link = current.topic_links[0]
        link.origin, link.evidence = "ai", current.summary
        facts["analysis"].result = result(
            language=current.language,
            content_type=current.content_type,
            content_format=current.content_format,
            ai_title=current.ai_title,
            ai_summary=current.ai_summary,
            ai_description=current.ai_description,
            topics=[
                {
                    "topic_id": link.topic_id,
                    "role": link.role,
                    "relevance": link.relevance,
                    "evidence": link.evidence,
                }
            ],
            tags=[],
        ).model_dump(mode="json")
        assert derive_publication_readiness(current, **facts).stage == "ready"
        current.publication_status = "published"
        current.published_to_feed_at = current.feed_at = NOW - timedelta(seconds=30 * (index + 1))
        current.editorial_revision += 1
        current.slug = f"reader-starter-{index}"
        current.origins[0].source.slug = "engineering-publisher"
        current.origins[0].source_metadata = {}
        items.append(current)
    return items


@pytest.mark.parametrize("selected", ["following", "latest", "empty"])
def test_starter_read_prefers_supplied_direct_follow_results_and_falls_back_only_when_empty(
    stories, selected
):
    session, owner = Mock(spec=Session), uuid.uuid4()
    session.scalars.side_effect = (
        [[stories[0]]]
        if selected == "following"
        else [[], [stories[0]] if selected == "latest" else []]
    )
    rows, kind = feeds.starter_articles(
        session,
        owner,
        FeedSettings(languages=["en"], content_types=["tutorial"]),
        4,
        "tutorial",
        stories[0].origins[0].source_id,
    )
    assert rows == ([] if selected == "empty" else [stories[0]])
    assert kind == ("following" if selected == "following" else "latest")
    assert session.scalars.call_count == (1 if selected == "following" else 2)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_continuation_cannot_combine_an_issued_cursor_with_a_different_explicit_generation(
    scenario, stories, monkeypatch
):
    user, state = scenario
    identifiers = [story.id for story in stories]

    def cached_ids(owner, revision, generation, offset, count):
        assert (owner, revision, generation) == (
            state.user_id,
            state.preference_revision,
            state.generation,
        )
        return identifiers[offset : offset + count]

    read = Mock(side_effect=cached_ids)
    monkeypatch.setattr(feeds, "generation_ids", read)
    entries = [recommendation(user, story, index) for index, story in enumerate(stories, 1)]
    page = request(
        user, FeedReads(state, article_pages=[list(zip(stories, entries, strict=True))]), limit=1
    )
    assert page.next_cursor and page.generation == state.generation
    assert [item.id for item in page.items] == [stories[0].id]
    continuation = FeedReads(state)
    with pytest.raises(HTTPException) as conflict:
        request(user, continuation, limit=1, cursor=page.next_cursor, generation=uuid.uuid4())
    assert (
        conflict.value.status_code == 422
        and conflict.value.detail == "Conflicting feed generations"
    )
    assert continuation.reads == ["state"]
    read.assert_called_once()
    continuation.commit.assert_not_called()


def test_metadata_only_daily_snapshot_read_preserves_saved_picks_and_never_claims_presentation(
    stories,
):
    owner, missing, session = uuid.uuid4(), uuid.uuid4(), Mock(spec=Session)
    current = stories[0]
    snapshot = UserMustRead(
        user_id=owner,
        selection_date=NOW.date(),
        timezone="Asia/Kolkata",
        presented_at=None,
        picks=[
            {"id": str(missing), "reason": "followed_topic"},
            {"id": str(current.id), "reason": "followed_source"},
        ],
    )
    before = copy.deepcopy(snapshot.picks)
    session.scalars.side_effect = [[current], [current.id]]
    articles, reasons, read_ids = must_reads.read_snapshot(
        session,
        snapshot,
        FeedSettings(languages=["en"], content_types=["tutorial"]),
        include_details=False,
    )
    assert articles == [current]
    assert reasons == {str(current.id): "followed_source"} and read_ids == [current.id]
    assert snapshot.picks == before and snapshot.presented_at is None
    session.add.assert_not_called()
    session.commit.assert_not_called()
