"""Tag identity reuse follows committed/transaction-local catalog revisions."""

import pytest
from devfeed_core.models import Tag
from devfeed_core.source_tags import resolve_source_tags
from sqlalchemy import event, insert, text

pytestmark = pytest.mark.integration


def test_repeated_resolution_loads_only_identity_columns_once(database):
    with database.begin() as session:
        for start in range(0, 3000, 300):
            session.execute(
                insert(Tag).values(
                    [
                        dict(
                            name=f"Catalog {index}",
                            slug=f"catalog-{index}",
                            aliases=[f"Alias {index}"],
                        )
                        for index in range(start, start + 300)
                    ]
                )
            )
    statements = []
    with database() as session:
        engine = session.get_bind()

        def capture(conn, cursor, statement, parameters, context, executemany):
            if "FROM tags ORDER BY" in statement:
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            for _ in range(10):
                resolved, created = resolve_source_tags(session, ["Alias 42", "Catalog 99"])
                assert len(resolved) == 2 and created == 0
            assert len(statements) == 1
            assert "topic_match" not in statements[0]
            assert not session.identity_map
        finally:
            event.remove(engine, "before_cursor_execute", capture)


def test_raw_edits_deletions_and_rollback_invalidate_identity_snapshot(database):
    with database.begin() as session:
        tag = Tag(name="Canonical", slug="canonical", aliases=["Old alias"])
        session.add(tag)
        session.flush()
        identifier = tag.id
    with database() as session:
        assert resolve_source_tags(session, ["Old alias"])[0]["old alias"] == identifier
        session.execute(
            text("UPDATE tags SET aliases=ARRAY['New alias'] WHERE id=:id"), {"id": identifier}
        )
        assert resolve_source_tags(session, ["New alias"])[0]["new alias"] == identifier
        session.rollback()
        assert resolve_source_tags(session, ["Old alias"])[0]["old alias"] == identifier
        session.execute(text("DELETE FROM tags WHERE id=:id"), {"id": identifier})
        resolved, created = resolve_source_tags(session, ["Canonical"])
        assert created == 1 and resolved["canonical"] != identifier
