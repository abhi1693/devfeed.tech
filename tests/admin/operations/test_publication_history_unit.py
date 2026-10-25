"""Private publication history stays parent-scoped, searchable and paginated."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_admin_api import automation
from devfeed_http.pagination import ListQuery
from sqlalchemy.dialects import postgresql


@pytest.mark.parametrize("kind", ["article-decisions", "source-policy"])
@pytest.mark.parametrize("search", ["", "manual"])
def test_publication_history_validates_parent_and_keeps_scope_with_optional_search(kind, search):
    identifier, queries = uuid.uuid4(), []
    rows = [{"id": uuid.uuid4()}, {"id": uuid.uuid4()}]
    values = iter([identifier, len(rows)])

    def scalar(query):
        queries.append(query)
        return next(values)

    def scalars(query):
        queries.append(query)
        return SimpleNamespace(all=lambda: rows)

    session = SimpleNamespace(scalar=scalar, scalars=scalars)
    listing = ListQuery(q=search, sort=None, limit=25, offset=0)
    function = (
        automation.publication_decisions
        if kind == "article-decisions"
        else automation.publication_policy_history
    )
    result = function(identifier, session, listing)
    assert result == {"items": rows, "total": 2, "limit": 25, "offset": 0}
    assert len(queries) == 3
    parent, counted, selected = queries
    parent_sql = str(parent)
    assert (
        "SELECT articles.id" in parent_sql
        if kind == "article-decisions"
        else "SELECT sources.id" in parent_sql
    )
    assert identifier in parent.compile().params.values()
    assert identifier in counted.compile().params.values()
    assert identifier in selected.compile().params.values()
    sql = str(selected.compile(dialect=postgresql.dialect()))
    assert "created_at DESC" in sql and "LIMIT" in sql and "OFFSET" in sql
    assert ("ILIKE" in sql) is bool(search)
    if kind == "article-decisions":
        assert "article_publication_decisions.article_id =" in sql
    else:
        assert "source_publication_policy_reviews.source_id =" in sql
