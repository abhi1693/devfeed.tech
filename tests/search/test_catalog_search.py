"""Catalog search stays global while readers fetch only a bounded page."""

import uuid

import pytest
from devfeed_core.models import Source, Topic
from sqlalchemy import insert

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["topics", "sources"])
def test_catalog_search_filters_before_pagination_and_escapes_wildcards(client, database, kind):
    names = [f"Early {index:03}" for index in range(120)] + [
        "Late Match A",
        "Late Match B",
        "100%_literal",
    ]
    model = Topic if kind == "topics" else Source
    with database.begin() as session:
        for index, name in enumerate(names):
            values = dict(id=uuid.uuid4(), name=name, slug=f"catalog-{index}")
            if kind == "topics":
                values.update(kind="technology", status="active")
            else:
                values.update(
                    feed_url=f"https://example.test/{index}/feed",
                    source_type="publisher",
                    approval_status="approved",
                    enabled=True,
                )
            session.execute(insert(model).values(**values))
    first = client.get(f"/v1/{kind}", params={"limit": 60}).json()
    assert len(first) == 60
    assert "Late Match A" not in [row["name"] for row in first]
    for offset, expected in [(0, ["Late Match A"]), (1, ["Late Match B"]), (2, [])]:
        response = client.get(
            f"/v1/{kind}", params={"q": "  lAtE mAtCh  ", "limit": 1, "offset": offset}
        )
        assert response.status_code == 200
        assert [row["name"] for row in response.json()] == expected
    literal = client.get(f"/v1/{kind}", params={"q": "%_"})
    assert [row["name"] for row in literal.json()] == ["100%_literal"]
    assert client.get(f"/v1/{kind}", params={"q": "x" * 201}).status_code == 422
