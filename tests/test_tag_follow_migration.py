"""Tag-follow migrations preserve existing feeds and safely remove tag-only state."""

import importlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.mark.integration
def test_tag_follow_migration_preserves_feeds_and_refreshes_only_affected_users(database):
    migration = importlib.import_module("migrations.versions.0026_user_tag_follows")
    user, unaffected, article, tag_article, tag = (uuid.uuid4() for _ in range(5))
    identifiers = {
        "user": user,
        "unaffected": unaffected,
        "article": article,
        "tag_article": tag_article,
        "tag": tag,
    }
    with database() as session, session.get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                # Start at the prior schema without changing the shared fixture's
                # committed revision; all DDL and test data roll back together.
                migration.downgrade()
                for identifier in (user, unaffected):
                    connection.execute(
                        text("""INSERT INTO user_accounts
                        (id,issuer,subject,organization_id,created_at,last_seen_at)
                        VALUES(:id,'https://identity.example',:subject,'migration',now(),now())"""),
                        {"id": identifier, "subject": str(identifier)},
                    )
                for identifier in (article, tag_article):
                    connection.execute(
                        text("""INSERT INTO articles
                        (id,canonical_url,url_hash,title,summary,review_status,
                        publication_status,content_type,feed_at,discovered_at)
                        VALUES(:id,:url,:hash,'Existing recommendation','','approved',
                        'published','article',now(),now())"""),
                        {
                            "id": identifier,
                            "url": f"https://example.test/migration/{identifier}",
                            "hash": identifier.hex,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO tags(id,name,slug,aliases) "
                        "VALUES(:tag,'Migration tag','migration-tag','{}')"
                    ),
                    identifiers,
                )
                connection.execute(
                    text("""INSERT INTO user_recommendations
                    (user_id,article_id,position,score,reason)
                    VALUES(:user,:article,0,40,'followed_source')"""),
                    identifiers,
                )
                before = connection.execute(text("SELECT * FROM user_recommendations")).one()

                migration.upgrade()
                inspector = inspect(connection)
                assert inspector.get_pk_constraint("user_tags")["constrained_columns"] == [
                    "user_id",
                    "tag_id",
                ]
                assert {
                    (
                        tuple(key["constrained_columns"]),
                        key["referred_table"],
                        key["options"]["ondelete"],
                    )
                    for key in inspector.get_foreign_keys("user_tags")
                } == {(("user_id",), "user_accounts", "CASCADE"), (("tag_id",), "tags", "CASCADE")}
                assert "ix_user_tags_tag_id" in {
                    index["name"] for index in inspector.get_indexes("user_tags")
                }
                assert next(
                    column
                    for column in inspector.get_columns("user_recommendations")
                    if column["name"] == "tag_id"
                )["nullable"]
                assert connection.execute(text("SELECT * FROM user_recommendations")).one() == (
                    *before,
                    None,
                )
                connection.execute(
                    text("""UPDATE user_recommendation_states SET candidates_dirty=false,
                    invalidated=false,next_refresh_at=now()+interval '6 hours'""")
                )
                scheduled = connection.scalar(
                    text(
                        "SELECT next_refresh_at FROM user_recommendation_states WHERE user_id=:user"
                    ),
                    identifiers,
                )
                connection.execute(
                    text("INSERT INTO user_tags VALUES(:user,:tag,now())"), identifiers
                )
                assert connection.execute(
                    text("""SELECT candidates_dirty,invalidated,next_refresh_at
                    FROM user_recommendation_states WHERE user_id=:user"""),
                    identifiers,
                ).one() == (True, False, scheduled)
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO user_tags VALUES(:user,:tag,now())"), identifiers
                    )
                connection.execute(
                    text("""INSERT INTO user_recommendations
                    (user_id,article_id,position,score,reason,tag_id)
                    VALUES(:user,:tag_article,1,40,'followed_tag',:tag)"""),
                    identifiers,
                )

                migration.downgrade()
                inspector = inspect(connection)
                assert "user_tags" not in inspector.get_table_names()
                assert "tag_id" not in {
                    column["name"] for column in inspector.get_columns("user_recommendations")
                }
                assert (
                    connection.execute(text("SELECT * FROM user_recommendations")).one() == before
                )
                assert connection.execute(
                    text("""SELECT invalidated,next_refresh_at<=now()
                    FROM user_recommendation_states WHERE user_id=:user"""),
                    identifiers,
                ).one() == (True, True)
                assert connection.execute(
                    text("""SELECT invalidated,next_refresh_at
                    FROM user_recommendation_states WHERE user_id=:unaffected"""),
                    identifiers,
                ).one() == (False, scheduled)
                migration.upgrade()
                assert connection.scalar(text("SELECT count(*) FROM user_tags")) == 0
                assert connection.scalar(text("SELECT count(*) FROM user_recommendations")) == 1
        finally:
            transaction.rollback()
