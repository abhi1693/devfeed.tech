"""Import parsing and preview decisions against supplied, current catalog data."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_core import topic_proposals
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Tag, Topic, TopicAnalysisJob, TopicProposal
from devfeed_core.topic_proposals import TopicDraft, TopicImport, TopicReview, parse_import
from pydantic import ValidationError

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
ACTOR = {"subject": "catalog-reviewer", "issuer": "https://identity.example"}


def draft(**changes):
    return {"name": "PostgreSQL", "slug": "postgresql", "kind": "database", **changes}


def catalog_topic(**changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            **TopicDraft.model_validate(draft()).model_dump(mode="json"),
            "status": "active",
            **changes,
        }
    )


@pytest.fixture
def preview(monkeypatch):
    def run(rows, *, catalog=(), pending=(), source="catalog.json"):
        # Supply read results, without emulating query filtering or database state.
        monkeypatch.setattr(topic_proposals, "catalogs", lambda _: {t.slug: t for t in catalog})
        pending_slugs = SimpleNamespace(scalars=lambda _: list(pending))
        body = TopicImport(format="json", content=json.dumps(rows), source_name=source)
        return topic_proposals.preview_import(pending_slugs, body)

    return run


@pytest.mark.parametrize(
    "facts",
    [
        [],
        [
            {
                "name": "License",
                "value": "PostgreSQL",
                "source_url": "https://postgresql.org/about/licence/",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    ],
)
def test_csv_facts_and_optional_blanks_reach_the_same_validated_draft_as_json(facts):
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(
        ["name", "slug", "kind", "aliases", "keywords", "facts", "description", "logo_url"]
    )
    writer.writerow(
        [
            "PostgreSQL",
            "postgresql",
            "database",
            " Postgres | | PG ",
            "sql | relational",
            json.dumps(facts) if facts else "",
            "  ",
            "",
        ]
    )

    rows = parse_import(
        TopicImport(format="csv", content=content.getvalue(), source_name="catalog.csv")
    )
    parsed = TopicDraft.model_validate(rows[0])

    assert parsed == TopicDraft.model_validate(
        draft(
            aliases=["Postgres", "PG"],
            keywords=["sql", "relational"],
            facts=facts,
            description=None,
            logo_url=None,
        )
    )


def test_unclosed_csv_quote_is_reported_as_invalid_csv():
    body = TopicImport(
        format="csv",
        content='name,slug,kind\n"PostgreSQL,postgresql,database',
        source_name="broken.csv",
    )

    with pytest.raises(ValueError, match="Invalid CSV import"):
        parse_import(body)


def test_csv_stops_an_oversized_batch_before_accepting_more_than_one_hundred_topics():
    body = TopicImport(
        format="csv",
        source_name="large.csv",
        content="name,slug,kind\n" + "PostgreSQL,postgresql,database\n" * 101,
    )

    with pytest.raises(ValueError, match="at most 100 topics"):
        parse_import(body)


def test_preview_preserves_omitted_metadata_but_allows_explicit_clearing(preview):
    existing = catalog_topic(
        description="Relational database",
        aliases=["Postgres"],
        keywords=["SQL"],
        website_url="https://postgresql.org/",
        logo_url="https://postgresql.org/logo.png",
        facts=[
            {
                "name": "License",
                "value": "PostgreSQL",
                "source_url": "https://postgresql.org/about/licence/",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    )
    preserved = preview([draft(name="PostgreSQL database")], catalog=[existing])
    cleared = preview(
        [draft(name="PostgreSQL database", description=None, logo_url=None, aliases=[], facts=[])],
        catalog=[existing],
    )

    assert preserved.can_submit and cleared.can_submit
    retained = preserved.rows[0].topic
    assert preserved.rows[0].action == "update"
    assert retained.description == existing.description
    assert retained.aliases == existing.aliases
    assert retained.keywords == existing.keywords
    assert retained.website_url == existing.website_url
    assert retained.logo_url == existing.logo_url
    assert retained.facts[0].value == "PostgreSQL"
    removed = cleared.rows[0].topic
    assert removed.description is None and removed.logo_url is None
    assert removed.aliases == [] and removed.facts == []
    assert removed.keywords == ["SQL"] and removed.website_url == existing.website_url
    assert existing.description == "Relational database" and existing.aliases == ["Postgres"]


def test_unchanged_rows_do_not_create_work_but_can_accompany_a_new_topic(preview):
    existing = catalog_topic()

    unchanged = preview([draft()], catalog=[existing])
    mixed = preview([draft(), draft(name="SQLite", slug="sqlite")], catalog=[existing])

    assert unchanged.rows[0].action == "unchanged"
    assert unchanged.rows[0].issues == [] and not unchanged.can_submit
    assert [row.action for row in mixed.rows] == ["unchanged", "create"]
    assert mixed.can_submit


def test_shared_aliases_are_search_terms_and_do_not_conflict_with_canonical_identity(preview):
    existing = catalog_topic(
        name="Continuous Delivery", slug="continuous-delivery", kind="practice", aliases=["CD"]
    )

    result = preview(
        [
            draft(
                name="Continuous Deployment",
                slug="continuous-deployment",
                kind="practice",
                aliases=["CD"],
            )
        ],
        catalog=[existing],
    )

    assert result.can_submit
    assert result.rows[0].issues == []
    assert result.rows[0].topic.aliases == ["CD"]


@pytest.mark.parametrize(
    "existing_name,row",
    [
        ("PostgreSQL", draft(name="POSTGRESQL", slug="another-database")),
        ("Postgres", draft(name="Other database", slug="postgres")),
    ],
)
def test_canonical_name_and_slug_share_the_identity_namespace(preview, existing_name, row):
    existing = catalog_topic(name=existing_name)

    result = preview([row], catalog=[existing])

    assert not result.can_submit
    assert any("matches existing topic" in issue for issue in result.rows[0].issues)


def test_duplicate_import_identities_and_pending_slugs_block_the_whole_preview(preview):
    result = preview([draft(), draft(name="Different label")], pending=["postgresql"])

    assert not result.can_submit
    assert all(any("pending proposal" in issue for issue in row.issues) for row in result.rows)
    assert "Duplicate slug in this import" in result.rows[1].issues
    assert any("matches imported row 1" in issue for issue in result.rows[1].issues)


@pytest.mark.parametrize("status", ["proposed", "rejected"])
def test_import_updates_do_not_activate_or_rewrite_inactive_topics(preview, status):
    existing = catalog_topic(status=status)

    result = preview([draft(description="New description")], catalog=[existing])

    assert not result.can_submit
    assert "Only active topics can be updated through imports" in result.rows[0].issues
    assert existing.status == status and existing.description is None


def test_invalid_rows_keep_their_positions_and_cannot_supply_review_authority(preview):
    result = preview(
        [draft(), draft(name="SQLite", slug="sqlite", status="active", created_by=ACTOR)]
    )

    assert [row.row for row in result.rows] == [1, 2]
    assert [row.action for row in result.rows] == ["create", "invalid"]
    assert result.rows[1].topic is None
    assert any(issue.startswith("status:") for issue in result.rows[1].issues)
    assert any(issue.startswith("created_by:") for issue in result.rows[1].issues)
    assert not result.can_submit


@pytest.mark.parametrize("change", ["source", "import", "catalog"])
def test_preview_token_changes_when_input_or_catalog_changes(preview, change):
    existing = catalog_topic()
    original = preview([draft(name="SQLite", slug="sqlite")], catalog=[existing])
    unchanged = preview([draft(name="SQLite", slug="sqlite")], catalog=[existing])
    if change == "catalog":
        existing.description = "A later catalog edit"
    altered = preview(
        [
            draft(
                name="SQLite",
                slug="sqlite",
                **({"description": "New import description"} if change == "import" else {}),
            )
        ],
        catalog=[existing],
        source="different.json" if change == "source" else "catalog.json",
    )

    assert original.preview_token == unchanged.preview_token
    assert altered.preview_token != original.preview_token
    assert original.can_submit and altered.can_submit


def test_rejection_cannot_apply_an_otherwise_valid_topic_draft():
    with pytest.raises(ValidationError, match="Rejection cannot apply topic fields"):
        TopicReview(decision="rejected", topic=TopicDraft.model_validate(draft()))


def enrichment_reads(topic, articles, tags):
    """Provide already eligible evidence; query eligibility belongs to integration tests."""
    return SimpleNamespace(
        get=lambda *_: topic,
        scalars=lambda _: SimpleNamespace(all=lambda: list(articles)),
        execute=lambda _: list(tags),
    )


def article_evidence(identifier):
    return SimpleNamespace(
        id=uuid.UUID(int=identifier),
        title=f"Database engineering {identifier}",
        canonical_url=f"https://engineering.example/articles/{identifier}",
    )


def test_enrichment_deduplicates_aliases_per_article_and_requires_two_distinct_articles():
    topic = catalog_topic(keywords=["SQL"])
    first, second = article_evidence(1), article_evidence(2)
    postgres = Tag(name="PostgreSQL", slug="postgresql", aliases=["Postgres", "SQL"])
    synonym = Tag(name="Postgres", slug="postgres", aliases=["PostgreSQL"])
    rare = Tag(name="SQLite", slug="sqlite", aliases=[])
    reads = enrichment_reads(
        topic,
        [first, second],
        [
            (first.id, postgres),
            (second.id, postgres),
            (second.id, synonym),
            (first.id, rare),
        ],
    )

    result = topic_proposals.preview_enrichment(reads, topic.id)

    assert result.articles_examined == 2
    assert [suggestion.keyword for suggestion in result.suggestions] == ["Postgres", "PostgreSQL"]
    assert [suggestion.article_count for suggestion in result.suggestions] == [2, 2]
    assert all(len(suggestion.articles) == 2 for suggestion in result.suggestions)
    assert topic.keywords == ["SQL"]  # A preview never applies its suggestions.


def test_enrichment_ranks_by_distinct_evidence_then_term_and_caps_examples_at_five():
    topic = catalog_topic()
    articles = [article_evidence(identifier) for identifier in range(1, 7)]
    common = Tag(name="Database", slug="database", aliases=["Query engine"])
    less_common = Tag(name="Transactions", slug="transactions", aliases=[])
    tags = [(article.id, common) for article in articles]
    tags += [(article.id, less_common) for article in articles[:3]]

    result = topic_proposals.preview_enrichment(enrichment_reads(topic, articles, tags), topic.id)

    assert [
        (suggestion.keyword, suggestion.article_count) for suggestion in result.suggestions
    ] == [
        ("Database", 6),
        ("Query engine", 6),
        ("Transactions", 3),
    ]
    assert result.suggestions[0].articles == [
        {"id": str(article.id), "title": article.title, "url": article.canonical_url}
        for article in articles[:5]
    ]


@pytest.mark.parametrize("existing_count,expected_count", [(0, 25), (97, 3), (100, 0)])
def test_enrichment_respects_both_the_suggestion_limit_and_remaining_keyword_capacity(
    existing_count, expected_count
):
    topic = catalog_topic(keywords=[f"Existing {index}" for index in range(existing_count)])
    articles = [article_evidence(1), article_evidence(2)]
    tags = [
        Tag(name=f"Keyword {index:02}", slug=f"keyword-{index}", aliases=[]) for index in range(26)
    ]
    evidence = [(article.id, tag) for tag in tags for article in articles]

    result = topic_proposals.preview_enrichment(
        enrichment_reads(topic, articles, evidence), topic.id
    )

    assert len(result.suggestions) == expected_count
    assert [suggestion.keyword for suggestion in result.suggestions] == [
        f"Keyword {index:02}" for index in range(expected_count)
    ]
    assert len(topic.keywords) == existing_count


def test_empty_enrichment_evidence_cannot_invent_keywords_or_request_tag_rows():
    topic = catalog_topic()
    reads = enrichment_reads(topic, [], [])
    reads.execute = lambda _: pytest.fail("No article evidence exists to load tags for")

    result = topic_proposals.preview_enrichment(reads, topic.id)

    assert result.articles_examined == 0 and result.suggestions == []
    assert result.topic == topic_proposals.topic_draft(topic)


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("with_analysis", [False, True])
def test_proposal_view_preserves_review_snapshots_and_hashes_only_proposed_content(
    reviewed, with_analysis
):
    before = catalog_topic(description="Before review")
    after = catalog_topic(id=before.id, description="Reviewed description")
    proposal = TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        topic_id=before.id if reviewed else None,
        slug="postgresql",
        action="update" if reviewed else "create",
        origin="import",
        source_name="catalog.json",
        proposed=draft(description="Proposed description"),
        baseline=topic_proposals.snapshot(before) if reviewed else None,
        evidence=[{"row": 1, "format": "json"}],
        status="approved" if reviewed else "pending",
        created_at=NOW,
        created_by=ACTOR,
        reviewed_at=NOW if reviewed else None,
        reviewed_by=ACTOR if reviewed else None,
        review_note="Reviewed" if reviewed else None,
        applied=topic_proposals.snapshot(after) if reviewed else None,
    )
    analysis = (
        TopicAnalysisJob(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            input_hash=snapshot_hash(proposal.proposed),
            input_snapshot={"topic": proposal.proposed},
            requested_by=ACTOR,
            prompt_version="test-proposal-projection-v1",
            status="succeeded",
            attempts=1,
            available_at=NOW,
            model="test-model",
            created_at=NOW,
            finished_at=NOW,
            outcome="enriched",
            error=None,
            result={
                "reasons": ["Source-backed description"],
                "internal_detail": "not part of the projection",
            },
        )
        if with_analysis
        else None
    )

    view = topic_proposals.proposal_view(proposal, analysis)
    original_hash = view.content_hash
    if not reviewed:
        proposal.evidence = [*proposal.evidence, {"research_note": "Another source receipt"}]
        assert topic_proposals.proposal_view(proposal, analysis).content_hash == original_hash
        proposal.proposed = draft(description="Changed proposed description")
        assert topic_proposals.proposal_view(proposal, analysis).content_hash != original_hash
    assert view.created_by == ACTOR and view.evidence == [{"row": 1, "format": "json"}]
    if reviewed:
        assert view.before.description == "Before review"
        assert view.applied.description == "Reviewed description"
    else:
        assert view.before is None and view.applied is None
    if with_analysis:
        assert view.analysis.reasons == ["Source-backed description"]
        assert "internal_detail" not in view.analysis.model_dump()
    else:
        assert view.analysis is None
