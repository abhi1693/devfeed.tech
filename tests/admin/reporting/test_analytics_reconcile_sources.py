"""Scalar keysets and dependency fingerprints never certify partial observations."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_core import analytics_reconcile_sources as sources
from psycopg import sql

NOW = datetime(2026, 10, 29, 16, tzinfo=UTC)
ZERO = str(uuid.UUID(int=0))
ONE = str(uuid.UUID(int=1))


@pytest.fixture
def connection():
    calls = []
    state = SimpleNamespace(
        ids=[ZERO, ONE],
        base=[{"id": ZERO, "version": "1"}, {"id": ONE, "version": "2"}],
        catalogs=[
            {"name": "tags", "revision": "tag-v1"},
            {"name": "topics", "revision": "topic-v1"},
        ],
        dependencies={},
    )

    async def execute(query, parameters=None):
        rendered = query.as_string() if isinstance(query, sql.Composable) else query
        calls.append((rendered, parameters))
        if "reconcile-card-ids" in rendered:
            rows = [{"id": value} for value in state.ids]
        elif "reconcile-card-base" in rendered:
            rows = state.base
        elif "catalog_revisions" in rendered:
            rows = state.catalogs
        else:
            name = rendered.split("reconcile-card-dependency:", 1)[1].split(" ", 1)[0]
            rows = state.dependencies.get(
                name, [{"id": row["id"], "versions": [], "incomplete": False} for row in state.base]
            )

        async def fetchall():
            return rows

        return SimpleNamespace(fetchall=fetchall)

    return SimpleNamespace(execute=execute, state=state, calls=calls)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", list(sources.TABLES))
@pytest.mark.parametrize("current_only", [False, True])
async def test_first_identity_page_includes_zero_uuid_and_uses_bounded_scalar_sql(
    connection, kind, current_only
):
    assert await sources.card_ids(
        connection, kind, None, 2, current_only=current_only, now=NOW
    ) == [ZERO, ONE]
    query, parameters = connection.calls[0]
    assert f'FROM "{sources.TABLES[kind]}"' in query
    assert "SELECT id::text AS id" in query
    assert f'ORDER BY "{sources.TABLES[kind]}"."id" LIMIT %s' in query
    assert "id>%s" not in query and parameters[-1] == 2
    if current_only and kind in sources.CURRENT_FILTERS:
        count = 3 if kind == "article" else 1
        assert parameters == [NOW - timedelta(days=7)] * count + [2]
        assert sources.CURRENT_FILTERS[kind] in query
    else:
        assert parameters == [2] and "WHERE true" in query


@pytest.mark.anyio
async def test_resumed_identity_page_uses_a_typed_exclusive_uuid_cursor(connection):
    await sources.card_ids(connection, "article", ZERO, 200, current_only=True, now=NOW)
    query, parameters = connection.calls[0]
    assert "id>%s::uuid AND ((publication_status" in query
    assert parameters == [ZERO, *([NOW - timedelta(days=7)] * 3), 200]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "unknown"},
        {"limit": 0},
        {"limit": 201},
        {"limit": True},
        {"cursor": "'; DROP TABLE articles"},
        {"current_only": 1},
        {"now": NOW.replace(tzinfo=None)},
    ],
)
async def test_identity_validation_rejects_unsafe_or_unbounded_reads_before_sql(
    connection, updates
):
    options = dict(kind="article", cursor=None, limit=100, current_only=True, now=NOW) | updates
    with pytest.raises(ValueError):
        await sources.card_ids(connection, **options)
    assert not connection.calls


@pytest.mark.anyio
@pytest.mark.parametrize("kind", list(sources.TABLES))
async def test_fingerprints_are_stable_and_use_only_bounded_row_version_metadata(connection, kind):
    first = await sources.card_fingerprints(connection, kind, [ZERO, ONE, ONE])
    assert set(first) == {ZERO, ONE} and all(len(value) == 64 for value in first.values())
    assert await sources.card_fingerprints(connection, kind, [ZERO, ONE]) == first
    for query, _ in connection.calls:
        assert all(
            field not in query for field in ("c.result", "input_snapshot", "c.text", "payload")
        )
        if "reconcile-card-dependency" in query:
            assert "LEFT JOIN LATERAL" in query and "LIMIT 201" in query
            assert "count(d.token)>200" in query
    if kind == "article":
        assert any("catalog_revisions" in query for query, _ in connection.calls)


@pytest.mark.anyio
async def test_base_child_parent_and_catalog_versions_and_deletions_invalidate_fingerprints(
    connection,
):
    original = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    connection.state.base[0]["version"] = "3"
    changed = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    assert changed[ZERO] != original[ZERO] and changed[ONE] == original[ONE]
    connection.state.dependencies["origins"] = [
        {"id": ZERO, "versions": ["origin:1:parent-source:7"], "incomplete": False},
        {"id": ONE, "versions": [], "incomplete": False},
    ]
    child = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    assert child[ZERO] != changed[ZERO] and child[ONE] == changed[ONE]
    connection.state.dependencies["origins"][0]["versions"] = ["origin:1:parent-source:8"]
    parent = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    assert parent[ZERO] != child[ZERO]
    connection.state.dependencies["origins"][0]["versions"] = []
    assert await sources.card_fingerprints(connection, "article", [ZERO, ONE]) == changed
    connection.state.catalogs[0]["revision"] = "tag-v2"
    catalog = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    assert catalog[ZERO] != changed[ZERO] and catalog[ONE] != changed[ONE]
    connection.state.base = connection.state.base[1:]
    connection.state.dependencies.pop("origins")
    missing = await sources.card_fingerprints(connection, "article", [ZERO, ONE])
    assert missing[ZERO] == "" and missing[ONE] == catalog[ONE]


@pytest.mark.anyio
async def test_dependency_order_does_not_change_an_authoritative_fingerprint(connection):
    connection.state.dependencies["research"] = [
        {"id": ZERO, "versions": ["job-b:2", "job-a:1"], "incomplete": False},
        {"id": ONE, "versions": [], "incomplete": False},
    ]
    before = await sources.card_fingerprints(connection, "topic-proposal", [ZERO, ONE])
    connection.state.dependencies["research"][0]["versions"].reverse()
    assert await sources.card_fingerprints(connection, "topic-proposal", [ZERO, ONE]) == before


@pytest.mark.anyio
@pytest.mark.parametrize("incomplete,tokens", [(True, ["job:1"]), (False, ["job:1"] * 201)])
async def test_overflow_never_certifies_an_incomplete_fingerprint(connection, incomplete, tokens):
    connection.state.dependencies["research"] = [
        {"id": ZERO, "versions": tokens, "incomplete": incomplete},
        {"id": ONE, "versions": [], "incomplete": False},
    ]
    result = await sources.card_fingerprints(connection, "topic-proposal", [ZERO, ONE])
    assert result[ZERO] == "" and len(result[ONE]) == 64


@pytest.mark.anyio
async def test_missing_dependencies_or_catalogs_force_rebuild_without_fabricating_coverage(
    connection,
):
    connection.state.dependencies["research"] = []
    assert await sources.card_fingerprints(connection, "topic-proposal", [ZERO, ONE]) == {
        ZERO: "",
        ONE: "",
    }
    connection.state.catalogs = []
    assert await sources.card_fingerprints(connection, "article", [ZERO, ONE]) == {
        ZERO: "",
        ONE: "",
    }
    connection.state.base = []
    connection.calls.clear()
    assert await sources.card_fingerprints(connection, "source", [ZERO]) == {ZERO: ""}
    assert len(connection.calls) == 1


@pytest.mark.anyio
async def test_empty_fingerprint_request_performs_no_sql(connection):
    assert await sources.card_fingerprints(connection, "article", []) == {}
    assert not connection.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,identifiers",
    [
        ("unknown", [ZERO]),
        ("article", "invalid"),
        ("article", [ZERO] * 201),
        ("article", ["not-a-uuid"]),
    ],
)
async def test_fingerprint_validation_runs_before_sql(connection, kind, identifiers):
    with pytest.raises(ValueError):
        await sources.card_fingerprints(connection, kind, identifiers)
    assert not connection.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ambiguous", ["base-duplicate", "base-foreign", "dependency-duplicate", "dependency-foreign"]
)
async def test_ambiguous_source_results_fail_closed(connection, ambiguous):
    if ambiguous.startswith("base"):
        connection.state.base.append(
            {"id": ZERO if ambiguous.endswith("duplicate") else "foreign", "version": "3"}
        )
    else:
        rows = [{"id": ZERO, "versions": [], "incomplete": False}] * 2
        if ambiguous.endswith("foreign"):
            rows[1] = {"id": "foreign", "versions": [], "incomplete": False}
        connection.state.dependencies["research"] = rows
    with pytest.raises(ValueError, match="Ambiguous analytical card"):
        await sources.card_fingerprints(connection, "topic-proposal", [ZERO, ONE])


@pytest.mark.integration
def test_native_source_fingerprints_detect_dependency_changes_deletions_and_overflow(database):
    import asyncio

    from devfeed_core.config import get_settings
    from devfeed_core.models import (
        ArticleOrigin,
        ArticleTag,
        ResearchVerificationJob,
        SourceCandidate,
        Tag,
        TopicDecisionRun,
    )
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from sqlalchemy import delete, update

    from tests.support import partner_matching, source_readiness, taxonomy_command_helpers

    owner = source_readiness.source(id=uuid.UUID(ZERO))
    article = partner_matching.article(
        id=uuid.UUID(ZERO), publication_status="unpublished", published_to_feed_at=None
    )
    tag = Tag(id=uuid.uuid4(), name="Fingerprint", slug="fingerprint")
    focal = taxonomy_command_helpers.topic("React")
    peer = taxonomy_command_helpers.topic("JavaScript")
    proposal = taxonomy_command_helpers.proposal(id=uuid.UUID(ZERO))
    research = taxonomy_command_helpers.relationship_job(focal, peer)
    relation = taxonomy_command_helpers.relationship_proposal(
        focal, peer, research, id=uuid.UUID(ZERO)
    )
    with database.begin() as session:
        session.add_all([owner, article, tag, focal, peer, proposal, research, relation])
        session.flush()
        session.add_all(
            [
                SourceCandidate(
                    id=uuid.UUID(ZERO), name="Candidate", identity_url="https://candidate.test"
                ),
                SourceCandidate(
                    id=uuid.UUID(ONE),
                    name="Linked",
                    identity_url="https://linked.test",
                    source_id=owner.id,
                ),
                ArticleTag(article_id=article.id, tag_id=tag.id),
                ResearchVerificationJob(id=research.id, relationships=True),
                TopicDecisionRun(proposal_id=proposal.id, input_hash="a" * 64),
            ]
        )

    async def read():
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as pg,
            pg.transaction(),
        ):
            await pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            await pg.execute("SET LOCAL statement_timeout='3s'")
            results = {}
            for kind in sources.TABLES:
                assert ZERO in await sources.card_ids(
                    pg, kind, None, 200, current_only=False, now=NOW
                )
                results[kind] = (await sources.card_fingerprints(pg, kind, [ZERO]))[ZERO]
                assert len(results[kind]) == 64
            return results

    before = asyncio.run(read())
    with database.begin() as session:
        session.execute(delete(ArticleTag).where(ArticleTag.article_id == article.id))
        session.execute(
            update(TopicDecisionRun)
            .where(TopicDecisionRun.proposal_id == proposal.id)
            .values(reason="changed")
        )
        session.execute(
            update(ResearchVerificationJob)
            .where(ResearchVerificationJob.id == research.id)
            .values(outcome="review_required")
        )
        session.execute(
            update(SourceCandidate)
            .where(SourceCandidate.id == uuid.UUID(ONE))
            .values(name="Changed linked candidate")
        )
    after = asyncio.run(read())
    assert after["article"] != before["article"]
    assert after["source"] != before["source"]
    assert after["topic-proposal"] != before["topic-proposal"]
    assert after["relationship-proposal"] != before["relationship-proposal"]
    assert after["candidate"] == before["candidate"]

    with database.begin() as session:
        session.add_all(
            [
                ArticleOrigin(
                    article_id=article.id,
                    source_id=owner.id,
                    entry_key=str(index),
                    original_url=f"https://publisher.test/{index}",
                )
                for index in range(201)
            ]
        )

    async def overflow():
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as pg,
            pg.transaction(),
        ):
            await pg.execute("SET TRANSACTION READ ONLY")
            assert await sources.card_fingerprints(pg, "article", [ZERO, ONE]) == {
                ZERO: "",
                ONE: "",
            }

    asyncio.run(overflow())


@pytest.mark.integration
def test_current_scan_discovers_lost_recent_terminal_cards_using_their_actual_terminal_time(
    database,
):
    import asyncio

    from devfeed_core.config import get_settings
    from devfeed_core.models import ArticleReview
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from tests.support import partner_matching, taxonomy_command_helpers

    old = NOW - timedelta(days=30)
    recent = NOW - timedelta(days=1)
    rejected = partner_matching.article(
        review_status="rejected",
        publication_status="unpublished",
        published_to_feed_at=None,
        discovered_at=old,
    )
    fallback = partner_matching.article(
        review_status="rejected",
        publication_status="unpublished",
        published_to_feed_at=None,
        discovered_at=recent,
    )
    old_rejection = partner_matching.article(
        review_status="rejected",
        publication_status="unpublished",
        published_to_feed_at=None,
        discovered_at=recent,
    )
    old_publication = partner_matching.article(discovered_at=old, published_to_feed_at=old)
    published = partner_matching.article(discovered_at=old, published_to_feed_at=recent)
    unknown_publication_time = partner_matching.article(
        discovered_at=old, published_to_feed_at=None
    )
    focal = taxonomy_command_helpers.topic("React")
    peer = taxonomy_command_helpers.topic("JavaScript")
    research = taxonomy_command_helpers.relationship_job(focal, peer)
    relation = taxonomy_command_helpers.relationship_proposal(
        focal, peer, research, created_at=old, status="rejected", reviewed_at=recent, reviewed_by={}
    )
    proposal = taxonomy_command_helpers.proposal(
        created_at=old, status="rejected", reviewed_at=recent, reviewed_by={}
    )
    with database.begin() as session:
        session.add_all(
            [
                rejected,
                fallback,
                old_rejection,
                old_publication,
                published,
                unknown_publication_time,
                focal,
                peer,
                research,
                relation,
                proposal,
            ]
        )
        session.flush()
        session.add_all(
            [
                ArticleReview(
                    article_id=rejected.id, action="reject", revision=1, created_at=recent
                ),
                ArticleReview(
                    article_id=old_rejection.id, action="reject", revision=1, created_at=old
                ),
            ]
        )

    async def read():
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as pg,
            pg.transaction(),
        ):
            await pg.execute("SET TRANSACTION READ ONLY")
            await pg.execute("SET LOCAL statement_timeout='3s'")
            selected = set(
                await sources.card_ids(pg, "article", None, 200, current_only=True, now=NOW)
            )
            assert selected == {
                str(rejected.id),
                str(fallback.id),
                str(published.id),
                str(unknown_publication_time.id),
            }
            assert await sources.card_ids(
                pg, "topic-proposal", None, 200, current_only=True, now=NOW
            ) == [str(proposal.id)]
            assert await sources.card_ids(
                pg, "relationship-proposal", None, 200, current_only=True, now=NOW
            ) == [str(relation.id)]

    asyncio.run(read())


@pytest.mark.integration
def test_native_current_article_pages_stop_at_the_uuid_index_limit_at_retained_scale(database):
    import asyncio
    import hashlib

    from devfeed_core.config import get_settings
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    # Randomly distributed keys and a two-thirds cursor model a partially completed
    # recovery. Thirty percent remain current, including recent rejection reviews.
    identifiers = {
        str(
            uuid.UUID(
                hashlib.md5(f"card-page:{number}".encode(), usedforsecurity=False).hexdigest()
            )
        ): number % 20 < 6
        for number in range(100_000)
    }
    ordered = sorted(identifiers)

    async def read():
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as pg:
            async with pg.transaction():
                # Use the migrated tables and their real indexes, constraints and
                # triggers. Batches keep fixture writes within the normal deadline.
                for start in range(0, 100_000, 5_000):
                    await pg.execute(
                        "INSERT INTO articles (id,canonical_url,url_hash,title,slug,summary,"
                        "review_status,publication_status,published_to_feed_at,content_type,"
                        "feed_at,discovered_at) "
                        "SELECT md5('card-page:'||n)::uuid,'https://card-page.test/'||n,"
                        "md5('card-page:'||n),'Card page '||n,'card-page-'||n,'',"
                        "CASE WHEN n%%20<2 THEN 'pending' "
                        "WHEN n%%20 IN (3,4) OR n%%20 BETWEEN 6 AND 13 "
                        "THEN 'rejected' ELSE 'approved' END,"
                        "CASE WHEN n%%20 IN (2,5) OR n%%20>=14 "
                        "THEN 'published' ELSE 'unpublished' END,"
                        "CASE WHEN n%%20=2 THEN %s::timestamptz WHEN n%%20=5 THEN NULL "
                        "ELSE %s::timestamptz END,'article',%s::timestamptz,"
                        "CASE WHEN n%%20=4 THEN %s::timestamptz ELSE %s::timestamptz END "
                        "FROM generate_series(%s::int,%s::int) n",
                        (
                            NOW - timedelta(days=1),
                            NOW - timedelta(days=30),
                            NOW,
                            NOW - timedelta(days=1),
                            NOW - timedelta(days=30),
                            start,
                            start + 4_999,
                        ),
                    )
                await pg.execute(
                    "INSERT INTO article_reviews (id,article_id,action,revision,created_at) "
                    "SELECT md5('card-review:'||n)::uuid,md5('card-page:'||n)::uuid,'reject',1,"
                    "CASE WHEN n%%20=3 THEN %s::timestamptz ELSE %s::timestamptz END "
                    "FROM generate_series(0,99999) n WHERE n%%20=3 OR n%%20 BETWEEN 6 AND 10",
                    (NOW - timedelta(days=1), NOW - timedelta(days=30)),
                )
            await pg.execute("ANALYZE articles,article_reviews")

            calls = []

            async def execute(query, parameters):
                calls.append((query, parameters))
                return await pg.execute(query, parameters)

            async with pg.transaction():
                await pg.execute("SET TRANSACTION READ ONLY")
                await pg.execute("SET LOCAL statement_timeout='5s'")
                for current_only in (False, True):
                    for cursor in (None, ordered[66_666]):
                        expected = [
                            value
                            for value in ordered
                            if (cursor is None or value > cursor)
                            and (not current_only or identifiers[value])
                        ][:100]
                        selected = await sources.card_ids(
                            SimpleNamespace(execute=execute),
                            "article",
                            cursor,
                            100,
                            current_only=current_only,
                            now=NOW,
                        )
                        assert selected == expected
                        query, parameters = calls[-1]
                        row = await (
                            await pg.execute(
                                sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {}").format(query),
                                parameters,
                            )
                        ).fetchone()
                        plan = row["QUERY PLAN"][0]["Plan"]
                        # A text output-alias sort consumes every remaining match
                        # before LIMIT. UUID key order can stop the index after 100.
                        assert plan["Node Type"] == "Limit"
                        scan = plan["Plans"][0]
                        assert scan["Node Type"] in {"Index Scan", "Index Only Scan"}
                        assert scan["Index Name"] == "articles_pkey"
                        assert scan["Actual Rows"] == 100
                        if cursor is not None:
                            assert "id >" in scan["Index Cond"]

    asyncio.run(read())


@pytest.mark.integration
def test_native_card_kind_pages_resume_without_omissions_and_preserve_current_filters(database):
    import asyncio

    from devfeed_core.config import get_settings
    from devfeed_core.models import SourceCandidate
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from tests.support import partner_matching, source_readiness, taxonomy_command_helpers

    identifiers = [uuid.uuid5(uuid.NAMESPACE_URL, f"card-kind-page:{i}") for i in range(205)]
    focal = taxonomy_command_helpers.topic("React")
    peer = taxonomy_command_helpers.topic("JavaScript")
    research = taxonomy_command_helpers.relationship_job(focal, peer)
    current = set()
    with database.begin() as session:
        session.add_all([focal, peer, research])
        for number, identifier in enumerate(identifiers):
            pending = number < 5
            recent = number % 2 == 1
            if pending or recent:
                current.add(str(identifier))
            review = {
                "id": identifier,
                "status": "pending" if pending else "rejected",
                "reviewed_at": NOW - timedelta(days=1 if recent else 30),
                "reviewed_by": {},
            }
            session.add_all(
                [
                    partner_matching.article(
                        id=identifier,
                        publication_status="unpublished" if pending else "published",
                        published_to_feed_at=NOW - timedelta(days=1 if recent else 30),
                    ),
                    source_readiness.source(
                        id=identifier,
                        slug=f"card-kind-{number}",
                        feed_url=f"https://card-kind.test/{number}.xml",
                    ),
                    SourceCandidate(
                        id=identifier,
                        name=f"Candidate {number}",
                        identity_url=f"https://candidate.test/{number}",
                    ),
                    taxonomy_command_helpers.proposal(slug=f"card-kind-{number}", **review),
                    taxonomy_command_helpers.relationship_proposal(
                        focal,
                        peer,
                        research,
                        relation=(
                            "uses_language",
                            "depends_on",
                            "implements",
                            "part_of",
                            "related_to",
                        )[number % 5],
                        **review,
                    ),
                ]
            )

    async def read():
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with (
            await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as pg,
            pg.transaction(),
        ):
            await pg.execute("SET TRANSACTION READ ONLY")
            await pg.execute("SET LOCAL statement_timeout='5s'")
            for kind in sources.TABLES:
                for current_only in (False, True):
                    selected = []
                    cursor = None
                    for _ in range(4):
                        page = await sources.card_ids(
                            pg, kind, cursor, 100, current_only=current_only, now=NOW
                        )
                        assert len(page) <= 100
                        if not page:
                            break
                        selected.extend(page)
                        cursor = page[-1]
                    else:
                        pytest.fail("Bounded card pagination did not finish")
                    expected = (
                        current
                        if current_only and kind in sources.CURRENT_FILTERS
                        else {str(value) for value in identifiers}
                    )
                    assert selected == sorted(expected)

    asyncio.run(read())
