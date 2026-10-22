"""Missing public identities fail closed; publisher diversity survives recency bands."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_api.latest import diverse_ids
from test_public_discovery_response_contracts_unit import public_api
from test_source_readiness_unit import NOW


@pytest.mark.parametrize("kind", ["topics", "tags"])
@pytest.mark.parametrize("by_id", [False, True])
def test_valid_but_unavailable_public_taxonomy_identity_returns_not_found(monkeypatch, kind, by_id):
    identity = str(uuid.uuid4()) if by_id else "removed-framework"
    with public_api(monkeypatch) as api:
        api.session.scalar.return_value = None
        response = api.client.get(f"/v1/{kind}/{identity}", params={"by_id": by_id})
        assert response.status_code == 404
        assert response.json() == {
            "detail": "Topic not found" if kind == "topics" else "Tag not found"
        }
        api.session.scalar.assert_called_once()
        api.session.commit.assert_not_called()


def test_new_recency_band_can_avoid_previous_publisher_without_reordering_that_publishers_stories():
    first, alternative = uuid.uuid4(), uuid.uuid4()
    rows = [
        SimpleNamespace(id=uuid.uuid4(), feed_at=NOW - timedelta(hours=index)) for index in range(8)
    ]
    rows.extend(
        SimpleNamespace(id=uuid.uuid4(), feed_at=NOW - timedelta(hours=index))
        for index in (26, 27, 28)
    )
    publishers = {row.id: first for row in rows}
    publishers[rows[9].id] = alternative
    ordered = diverse_ids(rows, publishers)
    assert ordered == [str(row.id) for row in rows[:8]] + [
        str(rows[index].id) for index in (9, 8, 10)
    ]
    assert [value for value in ordered if publishers[uuid.UUID(value)] == first] == [
        str(row.id) for row in rows if publishers[row.id] == first
    ]
