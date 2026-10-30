"""Native current-card observations stay bounded, detached and safe to export."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import analytics_article_cards as article_cards
from devfeed_core import analytics_blockers as blockers
from devfeed_core import analytics_card_builders as builders
from devfeed_core.analytics_card_types import PipelineCard, SourcePipelineCard
from devfeed_core.models import Article, ArticleOrigin, Source, SourceCandidate
from devfeed_core.source_lifecycle import SourceLifecycleFacts, SourceReadiness
from devfeed_core.urls import fingerprint

from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "observe,args",
    [
        (builders.article_rows, (list(uuid.uuid4() for _ in range(201)),)),
        (
            builders.source_rows,
            (list(uuid.uuid4() for _ in range(101)), list(uuid.uuid4() for _ in range(100))),
        ),
        (
            builders.proposal_rows,
            (list(uuid.uuid4() for _ in range(101)), list(uuid.uuid4() for _ in range(100))),
        ),
    ],
)
def test_observation_limits_apply_before_opening_a_database_session(observe, args):
    factory = Mock(side_effect=AssertionError("No oversized source transaction"))

    with pytest.raises(ValueError, match="at most 200"):
        observe(factory, *args, "policy", NOW)

    factory.assert_not_called()


def test_empty_and_duplicate_batches_do_not_invent_observations():
    factory = Mock(side_effect=AssertionError("Empty batches need no source transaction"))
    identifier = uuid.uuid4()

    assert builders.bounded_ids([identifier] * 201) == [identifier]
    assert builders.article_rows(factory, [], "policy", NOW) == []
    assert builders.source_rows(factory, [], [], "policy", NOW) == []
    assert builders.proposal_rows(factory, [], [], "policy", NOW) == []
    factory.assert_not_called()


def test_search_preserves_field_boundaries_unicode_and_technology_symbols():
    assert builders.normalized_search("Разработка, C++", "C# // 100%") == "разработка c++\nc# 100"
    assert builders.normalized_search("front", "end") == "front\nend"
    assert "front end" not in builders.normalized_search("front", "end")
    assert json.loads(builders.literal_search("100%", "___")) == ["100%", "___"]
    assert json.loads(builders.literal_search("first\nfield", "second")) == [
        "first\nfield",
        "second",
    ]


def test_url_search_preserves_public_article_paths_and_query_but_discards_credentials():
    public = "https://publisher.example/C%2B%2B/guide?language=en&part=2"
    private = (
        "https://user:password@publisher.example/article?part=2&api_key=secret&%74oken=other#secret"
    )

    assert builders.public_article_uri(public) == public
    assert builders.public_article_uri(private) == "https://publisher.example/article?part=2"
    assert builders.uri_origin(private) == "https://publisher.example"
    assert (
        builders.uri_origin("https://[2001:db8::1]:8443/private/token")
        == "https://[2001:db8::1]:8443"
    )
    assert builders.uri_origin("https://publisher.example:bad/private") == ""
    assert builders.uri_origin("javascript:private") == ""
    article = SimpleNamespace(title="Title", ai_title=None, canonical_url=public)
    assert (
        builders.article_search(article, "postgres locale normalized value")
        == "postgres locale normalized value"
    )
    article.canonical_url = private
    assert "secret" not in builders.article_search(article, "private secret")
    assert "part 2" in builders.article_search(article, "private secret")


def test_automation_clock_changes_only_displayed_schedule_not_lifecycle_or_retry_timer(
    processing_case,
):
    article, facts = processing_case
    facts["analysis"].status = "queued"
    facts["analysis"].available_at = facts["now"] + timedelta(minutes=20)
    readiness = builders.derive_publication_readiness(article, **facts)
    jobs = {
        name: facts[name] for name in ("enrichment", "analysis", "content", "discovery", "storage")
    }
    original = article_cards._card(article, readiness, jobs, taxonomy=facts["taxonomy"])
    original_row = builders._article_row(
        article, original, "policy", facts["now"], None, jobs=jobs.values()
    )
    transition = original_row["next_transition_at"]

    article.automation_next_check_at = facts["now"] + timedelta(seconds=10)
    refreshed = builders.derive_publication_readiness(article, **facts)
    updated = article_cards._card(article, refreshed, jobs, taxonomy=facts["taxonomy"])

    assert readiness == refreshed
    assert original.next_check_at != updated.next_check_at
    assert original.model_dump(exclude={"next_check_at"}) == updated.model_dump(
        exclude={"next_check_at"}
    )
    assert transition == facts["analysis"].available_at
    updated_row = builders._article_row(
        article, updated, "policy", facts["now"], None, jobs=jobs.values()
    )
    assert updated_row["next_transition_at"] == transition


def test_source_export_preserves_actions_without_exporting_private_urls(monkeypatch):
    source = Source(
        id=uuid.uuid4(),
        name="Private C++ Publisher",
        website_url="https://privateuser:privatepassword@publisher.example/private-site-token",
        feed_url="https://publisher.example/private-feed-token?api_key=private-key",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        consecutive_failures=3,
        created_at=NOW,
        updated_at=NOW,
    )
    facts = SourceLifecycleFacts(key=f"source:{source.id}", source=source, candidates=[])
    facts.readiness = SourceReadiness(stage="active", state="complete", can_ingest=True)
    facts.snapshot = "authoritative-snapshot"
    session = Mock()
    session.scalars.side_effect = [[source], []]

    @contextmanager
    def factory():
        yield session

    monkeypatch.setattr(
        builders, "source_readiness_batch", lambda *args, **kwargs: {facts.key: facts}
    )

    [row] = builders.source_rows(factory, [source.id], [], "policy", NOW)

    card = SourcePipelineCard.model_validate(row["payload"])
    assert card.snapshot == facts.snapshot and card.can_ingest
    assert card.website_url is None and card.feed_url is None
    assert row["source_type"] == "publisher" and row["consecutive_failures"] == 3
    assert (
        row["search_text"]
        == "private c++ publisher\nhttps publisher example\nhttps publisher example"
    )
    assert json.loads(row["literal_search_text"])[1:] == ["https://publisher.example"] * 2
    exported = json.dumps(row, default=str)
    for value in (
        "privateuser",
        "privatepassword",
        "private-site-token",
        "private-feed-token",
        "private-key",
    ):
        assert value not in exported
    assert "private-site-token" in source.website_url
    assert "private-feed-token" in source.feed_url


def test_candidate_export_discards_private_identity_path_and_credentials(monkeypatch):
    candidate = SourceCandidate(
        id=uuid.uuid4(),
        source_id=None,
        name="Candidate C#",
        identity_url="https://candidate:password@publisher.example/private-token?secret=private-key",
        approval_status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    facts = SourceLifecycleFacts(
        key=f"candidate:{candidate.id}", source=None, candidates=[candidate]
    )
    facts.readiness = SourceReadiness(
        stage="discovery", state="blocked", retry_targets=["discovery"]
    )
    facts.snapshot = "candidate-snapshot"
    session = Mock()
    session.scalars.side_effect = [[], [candidate]]

    @contextmanager
    def factory():
        yield session

    monkeypatch.setattr(
        builders, "source_readiness_batch", lambda *args, **kwargs: {facts.key: facts}
    )

    [row] = builders.source_rows(factory, [], [candidate.id], "policy", NOW)

    card = SourcePipelineCard.model_validate(row["payload"])
    assert card.kind == "candidate" and card.source_id is None
    assert card.website_url is None and card.feed_url is None
    assert card.retry_targets[0].stage == "discovery"
    assert row["review_pending"] == 1
    assert json.loads(row["literal_search_text"]) == [
        "Candidate C#",
        "https://publisher.example",
        "",
    ]
    exported = json.dumps(row, default=str)
    for value in ("password", "private-token", "private-key"):
        assert value not in exported


def test_old_terminal_articles_preserve_exact_review_metadata_without_hydrating_evidence(
    processing_case, monkeypatch
):
    article, facts = processing_case
    article.publication_status = "published"
    article.published_to_feed_at = facts["now"] - timedelta(days=30)
    article.review_status = "pending"
    session = Mock()
    session.execute.side_effect = [
        None,
        None,
        Mock(all=Mock(return_value=[(article, "normalized")])),
        Mock(all=Mock(return_value=[])),
    ]

    @contextmanager
    def factory():
        yield session

    monkeypatch.setattr(
        builders, "catalog", Mock(side_effect=AssertionError("No terminal evidence hydration"))
    )
    monkeypatch.setattr(
        article_cards, "_batch_facts", Mock(side_effect=AssertionError("No terminal job hydration"))
    )

    [row] = builders.article_rows(factory, [article.id], "policy", facts["now"])

    card = PipelineCard.model_validate(row["payload"])
    assert row["review_pending"] == 1
    assert card.stage == "published" and card.state == "complete"
    assert card.jobs.model_dump() == {"enrichment": None, "analysis": None, "images": None}
    assert row["terminal_at"] == article.published_to_feed_at
    assert row["blocker_codes"] == [] and not row["catalog_sensitive"]
    session.scalars.assert_not_called()


def test_pending_article_evaluates_detached_evidence_without_inventing_analysis(
    processing_case, monkeypatch
):
    previous, completed = processing_case
    completed["enrichment"].outcome = "metadata_only"
    source = previous.origins[0].source
    article = Article(
        id=previous.id,
        canonical_url=previous.canonical_url,
        url_hash=fingerprint(previous.canonical_url),
        title=previous.title,
        summary=previous.summary,
        ai_title=None,
        ai_summary=None,
        ai_description=None,
        language=None,
        content_type=None,
        content_format=None,
        classification_provenance={},
        review_status="pending",
        publication_status="unpublished",
        editorial_revision=0,
        discovered_at=previous.discovered_at,
        image_url=None,
        managed_image={},
        origins=[ArticleOrigin(source=source, source_id=source.id)],
        topic_links=[],
    )
    facts = {
        "enrichment": {article.id: completed["enrichment"]},
        "analysis": {},
        "content": {},
        "discovery": {},
        "storage": {},
    }
    session = Mock()
    session.execute.side_effect = [
        None,
        None,
        Mock(all=Mock(return_value=[(article, "angular routing")])),
        Mock(all=Mock(return_value=[])),
    ]
    session.scalars.return_value = []
    reading = []

    @contextmanager
    def factory():
        reading.append(True)
        try:
            yield session
        finally:
            reading.pop()

    monkeypatch.setattr(builders, "catalog", lambda _: completed["taxonomy"])
    monkeypatch.setattr(article_cards, "_batch_facts", Mock(return_value=facts))
    monkeypatch.setattr(
        builders,
        "get_settings",
        lambda: configuration(ai_enabled=True, image_storage_enabled=False),
    )
    monkeypatch.setattr(builders, "automation_blocker_rows", lambda *_: {})
    derive = builders.derive_publication_readiness

    def detached(*args, **kwargs):
        assert not reading
        session.expunge_all.assert_called_once_with()
        return derive(*args, **kwargs)

    monkeypatch.setattr(builders, "derive_publication_readiness", detached)

    [row] = builders.article_rows(factory, [article.id], "policy", completed["now"])

    assert row["stage"] == "analysis" and row["review_pending"] == 1
    assert row["terminal_at"] is None and not row["has_active_work"]
    assert "current_analysis_required" in row["payload"]["blockers"]
    assert "summary" not in row["payload"] and "content" not in row["payload"]
    session.scalars.assert_called_once()


def test_proposal_observation_exports_only_current_safe_identity_metadata(monkeypatch):
    identifier = uuid.uuid4()
    record = SimpleNamespace(
        id=identifier, status="pending", created_at=NOW, reviewed_at=None, title="New C++ topic"
    )
    session = Mock()
    session.execute.side_effect = [None, None, [record]]

    @contextmanager
    def factory():
        yield session

    classification = blockers.AutomationBlocker(
        code="evidence_unverified",
        label="Needs review",
        count=1,
        targets=[blockers.RecoveryTarget(id=identifier, title=record.title, kind="topic-proposal")],
    )
    classify = Mock(return_value=[classification])
    monkeypatch.setattr(builders, "automation_blockers", classify)

    [row] = builders.proposal_rows(factory, [identifier], [], "policy", NOW)

    classify.assert_called_once_with(
        session, [], topic_ids=[identifier], relationship_ids=[], target_limit=1
    )
    assert row["blocker_codes"] == ["evidence_unverified"]
    assert row["review_pending"] == 1 and row["state"] == "blocked"
    assert row["payload"] == {
        "id": str(identifier),
        "kind": "topic-proposal",
        "title": record.title,
        "status": "pending",
    }
    selected = str(session.execute.call_args_list[2].args[0])
    assert "topic_proposals.id IN" in selected
    assert "evidence" not in selected and "prompt" not in selected


def test_empty_blocker_classification_matches_pure_catalog_without_querying():
    session = Mock()
    expected = blockers.blocker_catalog(full_automation=False)

    assert blockers.automation_blockers(session, []) == expected
    assert len(expected) == 18 and len({item.code for item in expected}) == 18
    assert all(item.count == 0 and item.targets == [] for item in expected)
    session.execute.assert_not_called()
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
    expected[0].targets.append(
        blockers.RecoveryTarget(id=uuid.uuid4(), title="Changed local copy", kind="article")
    )
    assert blockers.blocker_catalog(full_automation=False)[0].targets == []


def test_blocker_classification_rejects_whole_population_requests():
    session = Mock()
    with pytest.raises(ValueError, match="explicit bounded"):
        blockers.automation_blockers(session, None)
    with pytest.raises(ValueError, match="at most 200"):
        blockers.automation_blockers(session, [], topic_ids=[uuid.uuid4() for _ in range(201)])
    session.execute.assert_not_called()


def test_blocker_classification_scopes_each_private_evidence_query_to_selected_ids():
    article, topic, relationship = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = Mock()
    session.execute.side_effect = [[], Mock(all=Mock(return_value=[]))]
    session.scalar.return_value = 0
    session.scalars.return_value = []

    blockers.automation_blockers(
        session,
        [article],
        topic_ids=[topic],
        relationship_ids=[relationship],
        target_limit=1,
    )

    statements = [
        (session.execute.call_args_list[0].args[0], article),
        (session.execute.call_args_list[1].args[0], topic),
        (session.scalar.call_args.args[0], relationship),
        (session.scalars.call_args.args[0], relationship),
    ]
    for statement, identifier in statements:
        assert [identifier] in statement.compile().params.values()
    assert "MATERIALIZED" in str(statements[0][0])


def test_direct_normalization_orders_revisions_and_keeps_semantic_deletions_stable():
    from devfeed_core import analytics_direct_cards as direct

    identity, source = uuid.uuid4(), uuid.uuid4()
    row = {
        "stage": "analysis",
        "state": "retry_wait",
        "source_id": source,
        "source_ids": [source],
        "created_at": NOW,
        "activity_at": NOW,
        "payload": {"title": "Article", "next_check_at": NOW},
        "blocker_codes": ["z", "a"],
        "consecutive_failures": -1,
    }
    first = direct.normalize_card(row, "policy", "article", identity, 7, NOW)
    later = direct.normalize_card(
        {**row, "payload": {"title": "Article", "next_check_at": NOW + timedelta(minutes=1)}},
        "policy",
        "article",
        identity,
        8,
        NOW + timedelta(minutes=1),
    )
    assert first["version"] < later["version"] and first["semantic_hash"] == later["semantic_hash"]
    assert first["source_id"] == str(source) and first["source_ids"] == [str(source)]
    assert first["blocker_codes"] == ["a", "z"] and first["consecutive_failures"] == 0
    assert first["shard"] == identity.bytes[0] and first["deleted"] == 0
    deleted = direct.normalize_card(None, "policy", "article", identity, 9, NOW)
    repeated = direct.normalize_card(
        None, "policy", "article", identity, 10, NOW + timedelta(days=1)
    )
    assert deleted["deleted"] == 1 and deleted["semantic_hash"] == repeated["semantic_hash"]
    with pytest.raises(ValueError):
        direct.identity("invalid")


@pytest.mark.parametrize(
    "kind", ["article", "source", "candidate", "topic-proposal", "relationship-proposal", "unknown"]
)
def test_direct_builder_dispatches_selected_ids_and_tombstones_missing_records(monkeypatch, kind):
    from devfeed_core import analytics_direct_cards as direct
    from devfeed_core import db

    first, missing = uuid.uuid4(), uuid.uuid4()
    factory = object()
    monkeypatch.setattr(db, "get_engine", lambda: object())
    monkeypatch.setattr(direct, "sessionmaker", Mock(return_value=factory))
    monkeypatch.setattr(direct, "pipeline_policy_version", lambda _settings: "policy")
    observed = []

    def rows(actual_factory, *args):
        assert actual_factory is factory
        observed.append(args)
        return [{"entity_id": first, "stage": "active", "payload": {"title": "Selected"}}]

    for name in ("article_rows", "source_rows", "proposal_rows"):
        monkeypatch.setattr(direct, name, rows)
    if kind == "unknown":
        with pytest.raises(ValueError, match="Unknown operator card kind"):
            direct.build_cards(object(), kind, [first, missing], 11)
        assert observed == []
        return
    result = direct.build_cards(object(), kind, [str(first), missing], 11)
    assert [row["deleted"] for row in result] == [0, 1]
    assert all(row["version"] == 11 << 64 and row["kind"] == kind for row in result)
    args = observed[0]
    selected = [first, missing]
    if kind == "article":
        assert args[0] == selected
    elif kind in {"source", "topic-proposal"}:
        assert args[:2] == (selected, [])
    else:
        assert args[:2] == ([], selected)


def test_direct_card_owners_include_both_association_images_without_unknown_population():
    from devfeed_core.analytics_direct_cards import card_identities

    a, b, source, candidate = [uuid.uuid4() for _ in range(4)]
    result = card_identities(
        [
            {
                "source_table": "article_origins",
                "before_record": {"article_id": a},
                "record": {"article_id": b},
            },
            {"source_table": "sources", "record": {"id": source}},
            {"source_table": "source_candidates", "record": {"source_id": source}},
            {"source_table": "source_candidates", "record": {"id": candidate}},
            {"source_table": "sources", "record": {}},
            {"source_table": "unrelated", "identity": {"id": uuid.uuid4()}},
        ]
    )
    assert result == {
        "article": {str(a), str(b)},
        "source": {str(source)},
        "candidate": {str(candidate)},
    }


def test_card_schema_is_canonical_and_finite_import_batches_offload_construction(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock

    from devfeed_core import analytics_direct_cards as direct

    async def run():
        client = SimpleNamespace(execute=AsyncMock(), insert=AsyncMock())
        from importlib.resources import files

        schema = (
            files("devfeed_core.clickhouse_migrations")
            .joinpath("0002_initial_analytics.sql")
            .read_text()
        )
        ddl = next(
            statement
            for statement in schema.split("-- statement")
            if "CREATE TABLE IF NOT EXISTS analytics_card_canonical" in statement
        )
        assert "ReplacingMergeTree(version)" in ddl and "TTL" not in ddl
        assert "analytics_card_canonical" in ddl and "manifest" not in ddl
        loop_thread = threading.get_ident()
        built = []

        def build(_settings, kind, keys, revision):
            assert threading.get_ident() != loop_thread
            built.append((kind, keys, revision))
            return [{"id": key} for key in keys]

        monkeypatch.setattr(direct, "build_cards", build)
        identifiers = [uuid.UUID(int=i + 1) for i in range(201)]
        await direct.publish_card_ids(client, None, "article", [], 1)
        assert client.insert.call_count == 0
        await direct.publish_card_ids(client, None, "article", [*identifiers, identifiers[0]], 12)
        assert [len(keys) for _, keys, _ in built] == [200, 1]
        assert [key for _, keys, _ in built for key in keys] == sorted(map(str, identifiers))
        assert all(kind == "article" and revision == 12 for kind, _, revision in built)
        assert client.insert.call_count == 2

    asyncio.run(run())
