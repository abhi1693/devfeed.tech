"""Proposal filters operate on the full catalog and the latest research run."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import TopicAnalysisJob, TopicProposal, utcnow

pytestmark = pytest.mark.integration


@pytest.fixture
def proposal_catalog(database):
    created = utcnow()
    definitions = [
        ("alpha", "language", "GitHub curated topics", "create", {}),
        ("beta", "technology", "GitHub curated topics", "create", {"keywords": []}),
        ("gamma", "technology", "GitHub curated topics", "update", {"website_url": None}),
        ("delta", "discipline", "Article keywords", "update", {"facts": []}),
        ("epsilon", "technology", "GitHub curated topics", "create", {"aliases": []}),
        ("zeta", "technology", "report%_2026.json", "create", {"description": None}),
        ("eta", "technology", "topics.json", "create", {}),
        ("theta", "language", "Archived catalog", "create", {}),
    ]
    values = {}
    with database.begin() as session:
        for slug, kind, source, action, changes in definitions:
            draft = {
                "name": f"{slug.title()} display name",
                "slug": slug,
                "kind": kind,
                "description": "Nested callbacks" if slug == "delta" else "A description",
                "aliases": ["Unique identity" if slug == "alpha" else "Alias"],
                "keywords": ["semantics" if slug == "alpha" else "keyword"],
                "website_url": "https://example.com",
                "logo_url": "https://example.com/logo.svg",
                "facts": [
                    {
                        "name": "Language",
                        "value": "Python",
                        "source_url": "https://example.com",
                        "retrieved_at": created.isoformat(),
                    }
                ],
                **changes,
            }
            if slug == "eta":
                draft = {key: draft[key] for key in ("name", "slug", "kind")}
            value = TopicProposal(
                batch_id=uuid.UUID(int=1 if source == "GitHub curated topics" else 2),
                slug=slug,
                action=action,
                origin="import",
                source_name=source,
                proposed=draft,
                created_at=created,
                created_by={"subject": "importer"},
                status="approved" if slug == "theta" else "pending",
                reviewed_at=created if slug == "theta" else None,
                reviewed_by={"subject": "reviewer"} if slug == "theta" else None,
                applied={"draft": draft} if slug == "theta" else None,
            )
            session.add(value)
            session.flush()
            values[slug] = value.id
        jobs = [
            ("beta", "failed", None, created),
            ("beta", "running", None, created),  # UUID breaks an equal timestamp tie.
            ("gamma", "succeeded", "enriched", created - timedelta(minutes=1)),
            ("gamma", "failed", None, created),
            ("delta", "queued", None, created),
            ("epsilon", "failed", None, created - timedelta(minutes=1)),
            ("epsilon", "succeeded", "enriched", created),
            ("zeta", "succeeded", "insufficient_evidence", created),
        ]
        for index, (slug, status, outcome, timestamp) in enumerate(jobs, 1):
            session.add(
                TopicAnalysisJob(
                    id=uuid.UUID(int=index),
                    proposal_id=values[slug],
                    status=status,
                    outcome=outcome,
                    created_at=timestamp,
                    input_hash="a" * 64,
                    input_snapshot={},
                    requested_by={},
                    prompt_version="test",
                )
            )
    return values


def fetch(client, **params):
    response = client.get(
        "/v1/admin/topic-proposals", params={"status": "pending", "sort": "slug", **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        ("not_run", ["alpha", "eta"]),
        ("queued", ["delta"]),
        ("running", ["beta"]),
        ("failed", ["gamma"]),
        ("enriched", ["epsilon"]),
        ("no_additions", ["zeta"]),
    ],
)
def test_analysis_filter_uses_latest_run(admin_client, proposal_catalog, analysis, expected):
    page = fetch(admin_client, analysis=analysis)
    assert page["total"] == len(expected)
    assert [item["proposed"]["slug"] for item in page["items"]] == expected
    for item in page["items"]:
        if analysis in {"queued", "running", "failed"}:
            assert item["analysis"]["status"] == analysis


def test_combined_filters_apply_before_count_and_pagination(admin_client, proposal_catalog):
    params = dict(kind="technology", source="GitHub curated topics", action="create")
    page = fetch(admin_client, **params, limit=1, offset=1)
    assert page["total"] == 2
    assert [item["proposed"]["slug"] for item in page["items"]] == ["epsilon"]
    assert fetch(admin_client, **params, analysis="running")["total"] == 1
    assert fetch(admin_client, **params, batch_id=str(uuid.UUID(int=2)))["total"] == 0
    assert fetch(admin_client, **params, missing="website_url")["total"] == 0
    assert fetch(admin_client, source="github")["total"] == 0  # Exact source selection.


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("ALPHA DISPLAY", ["alpha"]),
        ("unique identity", ["alpha"]),
        ("semantics", ["alpha"]),
        ("Nested callbacks", ["delta"]),
        ("%_", ["zeta"]),
    ],
)
def test_search_covers_metadata_and_escapes_wildcards(admin_client, proposal_catalog, q, expected):
    assert [item["proposed"]["slug"] for item in fetch(admin_client, q=q)["items"]] == expected


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("any", ["beta", "delta", "epsilon", "eta", "gamma", "zeta"]),
        ("description", ["eta", "zeta"]),
        ("keywords", ["beta", "eta"]),
        ("aliases", ["epsilon", "eta"]),
        ("website_url", ["eta", "gamma"]),
        ("logo_url", ["eta"]),
        ("facts", ["delta", "eta"]),
    ],
)
def test_missing_filters_cover_absent_null_and_empty_fields(
    admin_client, proposal_catalog, missing, expected
):
    page = fetch(admin_client, missing=missing)
    assert page["total"] == len(expected)
    assert [item["proposed"]["slug"] for item in page["items"]] == expected


def test_filter_choices_come_from_entire_catalog(admin_client, proposal_catalog):
    response = admin_client.get("/v1/admin/topic-proposals/filters")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "kinds": ["discipline", "language", "technology"],
        "sources": [
            "Archived catalog",
            "Article keywords",
            "GitHub curated topics",
            "report%_2026.json",
            "topics.json",
        ],
    }


@pytest.mark.parametrize(
    "params",
    [{"analysis": "unknown"}, {"action": "delete"}, {"missing": "name"}, {"kind": "x" * 101}],
)
def test_invalid_filter_values_are_rejected(admin_client, params):
    assert admin_client.get("/v1/admin/topic-proposals", params=params).status_code == 422
