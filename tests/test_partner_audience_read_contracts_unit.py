"""Audience composition uses private persisted facts and current taxonomy reads.

Supplied rows already satisfy account, freshness and status filters. These units
exercise composition and policy decisions, not PostgreSQL filter/lock semantics.
"""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_core import partner_audiences as audiences
from devfeed_core.partner_audiences import AudienceInput
from sqlalchemy.orm import Session
from test_partner_matching_workflow_unit import NOW, topic
from test_partner_placement_workflow_unit import account


@pytest.mark.parametrize("profile", [None, {"experience_level": "senior"}, {}])
def test_reader_context_combines_follow_stack_and_admitted_fresh_interests_once(profile):
    session = Mock(spec=Session)
    followed, stack, inferred = [uuid.uuid4() for _ in range(3)]
    session.scalar.return_value = profile
    session.scalars.side_effect = [[followed], [stack, followed], [inferred, stack]]
    current = audiences.reader_audience(session, uuid.uuid4(), NOW)
    if profile is None:
        assert current is None
        session.scalars.assert_not_called()
    else:
        assert current.topic_ids == {followed, stack, inferred}
        assert current.experience_level == profile.get("experience_level")


def test_anonymous_reader_does_not_load_private_profile_or_interest_state():
    session = Mock(spec=Session)
    assert audiences.reader_audience(session, None, NOW) is None
    assert session.mock_calls == []


@pytest.mark.parametrize("snapshot", ["current", "removed", "wrong_kind", "busy"])
def test_selected_audience_requires_every_current_topic_to_retain_its_dimension(snapshot):
    language, tool = uuid.uuid4(), uuid.uuid4()
    selection = AudienceInput(language_topic_ids=[language], tool_topic_ids=[tool])
    session = Mock(spec=Session)
    session.execute.return_value.all.return_value = (
        [] if snapshot == "busy" else [(language, "language"), (tool, "framework")]
    )
    if snapshot == "removed":
        session.execute.return_value.all.return_value.pop()
    elif snapshot == "wrong_kind":
        session.execute.return_value.all.return_value[0] = (language, "framework")
    assert audiences.audience_topics_current(session, selection, lock=True) is (
        snapshot == "current"
    )
    if snapshot != "current":
        with pytest.raises(ValueError, match="active languages"):
            audiences.validate_audience(session, "platinum", selection)


@pytest.mark.parametrize(
    "dimension,selection", [("languages", "account"), ("tools", "campaign"), ("tools", "none")]
)
def test_saved_unavailable_topic_can_be_resolved_for_editing_without_changing_approval(
    dimension, selection
):
    selected = topic(
        "Python" if dimension == "languages" else "API Checker",
        kind="language" if dimension == "languages" else "tool",
        status="rejected" if selection != "none" else "active",
    )
    key = "language_topic_ids" if dimension == "languages" else "tool_topic_ids"
    owner = account(audience={key: [str(selected.id)]} if selection == "account" else {})
    session = Mock(spec=Session)
    session.scalar.side_effect = [1] if selection == "account" else [selection == "campaign", 1]
    session.scalars.return_value = [selected]
    output = audiences.audience_topic_options(
        session, dimension, account=owner, topic_id=selected.id, limit=1
    )
    assert output == {
        "items": [
            {
                "id": selected.id,
                "name": selected.name,
                "slug": selected.slug,
                "kind": selected.kind,
                "status": selected.status,
            }
        ],
        "total": 1,
    }
    assert selected.status == ("active" if selection == "none" else "rejected")
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("search", ["", "  Python  "])
def test_topic_option_page_returns_preselected_active_taxonomy_in_response_shape(search):
    selected = topic("Python", kind="language")
    session = Mock(spec=Session)
    session.scalar.return_value = 1
    session.scalars.return_value = [selected]
    output = audiences.audience_topic_options(session, "languages", q=search, offset=0, limit=1)
    assert output["items"][0]["id"] == selected.id and output["total"] == 1
    assert output["items"][0]["kind"] == "language"


def test_no_audience_selection_needs_no_taxonomy_read():
    session = Mock(spec=Session)
    assert audiences.audience_topics_current(session, AudienceInput(), lock=True)
    session.execute.assert_not_called()
