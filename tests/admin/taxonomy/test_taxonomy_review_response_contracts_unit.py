"""Admin review responses preserve evidence, attribution and explicit publication gates.

Repository rows and enrichment/GitHub command results are supplied boundaries.
The real relationship review policy executes, while catalog locking and edge
writes stay domain boundaries rather than simulated SQL/transaction semantics.
"""

from unittest.mock import Mock

import pytest
from devfeed_admin_api import topic_proposals as imports
from devfeed_admin_api import topic_relationships as relationships
from devfeed_admin_api.auth import actor
from devfeed_core import topic_proposals, topic_relationships
from devfeed_core.models import ArticleTag

from tests.admin.taxonomy.test_taxonomy_command_adapters_unit import tag
from tests.support.admin_editorial import operator_api
from tests.support.article_readiness import processing_case as processing_case
from tests.support.reader_context import stories as stories
from tests.support.taxonomy_command_helpers import (
    NOW,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)


@pytest.fixture
def suggestion(monkeypatch):
    focal, peer = topic(kind="framework"), topic("JavaScript", kind="language")
    research = relationship_job(focal, peer)
    current = relationship_proposal(focal, peer, research)
    monkeypatch.setattr(relationships, "record", Mock(return_value=current))
    monkeypatch.setattr(topic_relationships, "lock_topics", Mock())
    monkeypatch.setattr(topic_relationships, "utcnow", lambda: NOW)
    edge_write = Mock()
    monkeypatch.setattr(topic_relationships, "edge_exists", Mock(return_value=False))
    monkeypatch.setattr(topic_relationships, "relate_topics", edge_write)
    with operator_api(relationships) as api:
        api.session.scalar.return_value = current
        api.session.scalars.return_value = [focal, peer]
        yield api, current, focal, peer, edge_write


@pytest.mark.parametrize("changed", [False, True])
def test_relationship_detail_displays_original_evidence_and_current_approval_eligibility(
    suggestion, changed
):
    api, current, focal, peer, edge_write = suggestion
    if changed:
        peer.description = "The language description changed after this research run."
    response = api.client.get(f"/v1/admin/topic-relationship-proposals/{current.id}")
    assert response.status_code == 200, response.text
    output = response.json()
    assert (output["topic_name"], output["related_topic_name"]) == (focal.name, peer.name)
    assert output["content_hash"] == topic_relationships.proposal_hash(current)
    assert output["evidence_quote"] == current.evidence_quote and output["status"] == "pending"
    assert output["can_approve"] is not changed
    assert output["approval_blocker"] == (
        "A topic changed after research. Reject this suggestion and run research again"
        if changed
        else None
    )
    edge_write.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_relationship_review_returns_attributed_terminal_history_without_another_approval(
    suggestion, decision
):
    api, current, focal, peer, edge_write = suggestion
    digest = topic_relationships.proposal_hash(current)
    note = "Compared the cited source with the current topic identities."
    response = api.client.post(
        f"/v1/admin/topic-relationship-proposals/{current.id}/review",
        json={"decision": decision, "expected_input_hash": digest, "note": note},
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["status"] == current.status == decision
    assert output["reviewed_by"] == current.reviewed_by == actor(api.admin)
    assert output["review_note"] == note and current.reviewed_at == NOW
    assert output["can_approve"] is False and output["approval_blocker"] is None
    assert output["content_hash"] == digest and output["evidence_url"] == current.evidence_url
    if decision == "approved":
        write = edge_write.call_args.args[1]
        assert (write.topic_id, write.related_topic_id, write.relation) == (
            focal.id,
            peer.id,
            "uses_language",
        )
        assert write.evidence_url == current.evidence_url
    else:
        edge_write.assert_not_called()
    api.session.commit.assert_called_once_with()


def test_empty_relationship_review_page_does_not_load_unrelated_topic_profiles(monkeypatch):
    page = {"items": [], "total": 0, "offset": 0, "limit": 25}
    monkeypatch.setattr(relationships, "paginate", Mock(return_value=page))
    with operator_api(relationships) as api:
        response = api.client.get("/v1/admin/topic-relationship-proposals")
        assert response.status_code == 200 and response.json() == page
        assert api.session.mock_calls == []


def test_keyword_preview_and_submission_keep_the_active_topic_unchanged_pending_review(
    monkeypatch, stories
):
    current = topic(
        "Angular",
        id=stories[0].topic_links[0].topic_id,
        kind="framework",
        keywords=["angular"],
    )
    components = tag(name="components", slug="components", aliases=[])
    # Operators retained this manual tag on both already-published stories.
    links = [
        ArticleTag(article_id=row.id, tag_id=components.id, origin="manual") for row in stories
    ]
    for row in stories:
        row.tags = [components]
    with operator_api(imports) as api:
        api.session.get.return_value = current
        api.session.scalars.return_value.all.return_value = stories
        api.session.execute.return_value = [(link.article_id, components) for link in links]
        response = api.client.post(f"/v1/admin/topics/{current.id}/enrichment/preview")
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["articles_examined"] == 2
        assert len(preview["preview_token"]) == 64
        assert len(preview["suggestions"]) == 1
        evidence = preview["suggestions"][0]
        assert evidence["keyword"] == "components" and evidence["article_count"] == 2
        assert {row["id"] for row in evidence["articles"]} == {str(row.id) for row in stories}
        api.session.commit.assert_not_called()
        draft = {**preview["topic"], "keywords": ["angular", "components"]}
        pending = proposal(
            current,
            origin="article_enrichment",
            source_name="Tags on approved, published articles",
            proposed=draft,
            evidence=[evidence],
            created_at=NOW,
            created_by=actor(api.admin),
        )
        command = Mock(return_value=pending)
        monkeypatch.setattr(topic_proposals, "submit_enrichment", command)
        response = api.client.post(
            f"/v1/admin/topics/{current.id}/enrichment",
            json={"preview_token": preview["preview_token"], "keywords": ["components"]},
        )
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["status"] == "pending" and output["applied"] is None
        assert output["before"]["keywords"] == ["angular"]
        assert output["proposed"]["keywords"] == ["angular", "components"]
        assert current.keywords == ["angular"] and current.status == "active"
        session, identifier, body, attribution = command.call_args.args
        assert session is api.session and identifier == current.id
        assert body.preview_token == preview["preview_token"] and body.keywords == ["components"]
        assert attribution == actor(api.admin)
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "body",
    [
        {"preview_token": "invalid", "keywords": ["components"]},
        {"preview_token": "a" * 64, "keywords": []},
    ],
)
def test_invalid_keyword_selection_is_rejected_before_the_enrichment_command(monkeypatch, body):
    command = Mock()
    monkeypatch.setattr(topic_proposals, "submit_enrichment", command)
    with operator_api(imports) as api:
        response = api.client.post(f"/v1/admin/topics/{topic().id}/enrichment", json=body)
        assert response.status_code == 422
        command.assert_not_called()
        api.session.commit.assert_not_called()


def test_github_pull_response_keeps_the_immutable_revision_and_next_batch_cursor(monkeypatch):
    result = imports.github_topics.GitHubPullResult(
        revision="a" * 40,
        total=55,
        processed=20,
        created=3,
        skipped=17,
        issues=[],
        next_offset=40,
    )
    command = Mock(return_value=result)
    monkeypatch.setattr(imports.github_topics, "pull_topics", command)
    with operator_api(imports) as api:
        response = api.client.post(
            "/v1/admin/topic-discovery/github", json={"revision": result.revision, "offset": 20}
        )
        assert response.status_code == 200 and response.json() == result.model_dump()
        session, body, attribution = command.call_args.args
        assert session is api.session and attribution == actor(api.admin)
        assert body.revision == result.revision and body.offset == 20
        api.session.commit.assert_called_once_with()
