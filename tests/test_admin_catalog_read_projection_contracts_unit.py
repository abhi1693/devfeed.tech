"""Catalog reads preserve scoped page counts, review context and current job annotations.

Repository pages and latest-job rows are supplied facts. No SQL filtering,
pagination ordering, latest-row selection or ownership predicates are simulated.
"""

from unittest.mock import Mock

import pytest
from devfeed_admin_api import source_imports, taxonomy, topic_proposals, topics
from devfeed_admin_api.auth import actor
from devfeed_core.models import ArticleImageJob, Topic
from devfeed_core.schemas import TagOut
from taxonomy_command_helpers import proposal, topic
from test_admin_editorial_policy_unit import operator_api
from test_admin_source_pipeline_contracts_unit import candidate
from test_admin_taxonomy_command_adapters_unit import tag
from test_admin_taxonomy_policy_unit import queued_metadata
from test_source_readiness_unit import job, source


@pytest.mark.parametrize("kind", ["tags", "topics"])
@pytest.mark.parametrize("empty", [False, True])
def test_catalog_page_keeps_the_global_count_and_exact_final_page_projection(
    monkeypatch, kind, empty
):
    module = taxonomy if kind == "tags" else topics
    current = tag() if kind == "tags" else topic()
    page = {
        "items": [] if empty else [current],
        "total": 37,
        "limit": 1,
        "offset": 37 if empty else 36,
    }
    monkeypatch.setattr(module, "paginate", Mock(return_value=page))
    with operator_api(module) as api:
        response = api.client.get(
            f"/v1/admin/{kind}", params={"limit": 1, "offset": page["offset"]}
        )
        assert response.status_code == 200, response.text
        output = response.json()
        assert (output["total"], output["limit"], output["offset"]) == (37, 1, page["offset"])
        expected = (
            TagOut.model_validate(current)
            if kind == "tags"
            else topics.AdminTopicOut.model_validate(current)
        )
        assert output["items"] == ([] if empty else [expected.model_dump(mode="json")])
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
def test_discovery_candidate_page_retains_attached_source_identity_and_global_count(empty):
    publisher = source()
    current = candidate(
        source_id=publisher.id,
        identity_url=publisher.website_url,
        name=publisher.name,
        selected_feed_id=None,
    )
    offset = 37 if empty else 36
    with operator_api(source_imports) as api:
        api.session.scalar.return_value = 37
        api.session.execute.return_value = [] if empty else [(current, publisher.approval_status)]
        response = api.client.get("/v1/admin/source-imports", params={"limit": 1, "offset": offset})
        assert response.status_code == 200, response.text
        output = response.json()
        assert (output["total"], output["offset"], output["limit"]) == (37, offset, 1)
        assert [item["id"] for item in output["items"]] == ([] if empty else [str(current.id)])
        if not empty:
            assert output["items"][0]["source_id"] == str(publisher.id)
            assert output["items"][0]["status"] == "pending"
            assert output["items"][0]["approval_status"] == "pending"
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("params", [{"limit": 101}, {"offset": -1}])
def test_invalid_discovery_page_bounds_stop_before_any_repository_read(params):
    with operator_api(source_imports) as api:
        response = api.client.get("/v1/admin/source-imports", params=params)
        assert response.status_code == 422 and response.json() == {"detail": "Invalid pagination"}
        api.session.scalar.assert_not_called()
        api.session.execute.assert_not_called()


@pytest.mark.parametrize("requested", [False, True])
def test_proposal_page_attaches_the_selected_analysis_to_its_proposal_without_exposing_inputs(
    monkeypatch, requested
):
    current = proposal()
    page = {"items": [current], "total": 1, "limit": 25, "offset": 0}
    monkeypatch.setattr(topic_proposals, "paginate", Mock(return_value=page))
    with operator_api(topic_proposals) as api:
        analysis = queued_metadata(current, actor(api.admin))
        api.session.scalars.return_value = [analysis] if requested else []
        response = api.client.get("/v1/admin/topic-proposals")
        assert response.status_code == 200, response.text
        output = response.json()
        assert output["total"] == 1 and len(output["items"]) == 1
        item = output["items"][0]
        assert item["id"] == str(current.id) and item["status"] == "pending"
        assert item["content_hash"] == topic_proposals.proposals.proposal_view(current).content_hash
        if requested:
            assert item["analysis"]["id"] == str(analysis.id)
            assert item["analysis"]["status"] == "queued"
        else:
            assert item["analysis"] is None
        assert "input_snapshot" not in response.text and "requested_by" not in response.text
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("status", [None, "queued", "failed"])
def test_topic_detail_combines_the_current_profile_with_its_selected_logo_job_status(
    monkeypatch, status
):
    current = topic(logo_url="https://react.example/logo.svg", managed_logo={})
    selected = (
        job(
            ArticleImageJob,
            status=status,
            topic_id=current.id,
            operation="topic-logo",
            image_url=current.logo_url,
            storage={},
            storage_version="v1",
        )
        if status
        else None
    )
    read = Mock(return_value=current)
    monkeypatch.setattr(topics, "record", read)
    with operator_api(topics) as api:
        api.session.scalar.return_value = selected
        response = api.client.get(f"/v1/admin/topics/{current.id}")
        assert response.status_code == 200, response.text
        output = response.json()
        assert output["id"] == str(current.id) and output["status"] == "active"
        assert output["logo_storage_status"] == status
        assert output["logo_storage_error"] == (selected.error if selected else None)
        read.assert_called_once_with(api.session, Topic, current.id)
        api.session.commit.assert_not_called()
