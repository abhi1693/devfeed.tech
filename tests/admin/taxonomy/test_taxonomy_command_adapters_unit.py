"""Taxonomy command adapters preserve validated fields and domain guard outcomes.

Domain operations and reference checks are explicit boundaries. Requested writes
and commit sequencing are observed; SQL predicates, cascades and locks are not simulated.
"""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_admin_api import taxonomy, topics
from devfeed_core.models import Tag, TopicRelation
from devfeed_core.schemas import TagPatch, TagWrite
from devfeed_core.services import OperationConflict, RecordNotFound

from tests.support.admin_editorial import operator_api
from tests.support.taxonomy_command_helpers import topic


def tag(**changes):
    return Tag(
        **{
            "id": uuid.uuid4(),
            "name": "Routing",
            "slug": "routing",
            "aliases": ["Router"],
            "topic_id": None,
            "auto_link_topic": False,
            "topic_match_revision": 1,
            "topic_match_status": "manual",
            "topic_match_checked_at": None,
            **changes,
        }
    )


@pytest.mark.parametrize("operation", ["create", "replace", "patch"])
def test_tag_commands_return_the_saved_domain_record_and_preserve_explicit_patch_clears(
    monkeypatch, operation
):
    current = tag(aliases=[] if operation == "patch" else ["Router"])
    body = (
        {"aliases": [], "topic_id": None, "auto_link_topic": False}
        if operation == "patch"
        else {
            "name": current.name,
            "slug": current.slug,
            "aliases": current.aliases,
            "auto_link_topic": False,
        }
    )
    name = "create_tag" if operation == "create" else "update_tag"
    command = Mock(return_value=current)
    monkeypatch.setattr(taxonomy.services, name, command)
    with operator_api(taxonomy) as api:
        path = "/v1/admin/tags" + ("" if operation == "create" else f"/{current.id}")
        method = {"create": "post", "replace": "put", "patch": "patch"}[operation]
        response = getattr(api.client, method)(path, json=body)
        assert response.status_code == (201 if operation == "create" else 200), response.text
        assert response.json()["id"] == str(current.id)
        assert response.json()["aliases"] == current.aliases
        args = command.call_args.args
        parsed = args[-1]
        assert args[0] is api.session
        assert isinstance(parsed, TagPatch if operation == "patch" else TagWrite)
        assert parsed.model_dump(exclude_unset=True) == body
        if operation != "create":
            assert args[1] == current.id
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "failure", [RecordNotFound("Tag not found"), OperationConflict("Tag identity changed")]
)
def test_failed_tag_update_keeps_domain_http_meaning_without_committing(monkeypatch, failure):
    command = Mock(side_effect=failure)
    monkeypatch.setattr(taxonomy.services, "update_tag", command)
    with operator_api(taxonomy) as api:
        response = api.client.patch(f"/v1/admin/tags/{uuid.uuid4()}", json={"aliases": []})
        assert response.status_code == (404 if isinstance(failure, RecordNotFound) else 409)
        assert response.json() == {"detail": str(failure)}
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("referenced", [False, True])
def test_tag_delete_respects_the_domain_reference_barrier_before_requesting_any_write(
    monkeypatch, referenced
):
    current = tag()
    require = Mock(return_value=current)
    barrier = Mock(side_effect=OperationConflict("Tag is used by articles") if referenced else None)
    monkeypatch.setattr(taxonomy, "record", require)
    monkeypatch.setattr(taxonomy, "prohibit_references", barrier)
    with operator_api(taxonomy) as api:
        response = api.client.delete(f"/v1/admin/tags/{current.id}")
        assert response.status_code == (409 if referenced else 204)
        require.assert_called_once_with(api.session, Tag, current.id, lock=True)
        barrier.assert_called_once()
        assert api.session.execute.call_count == int(not referenced)
        assert api.session.commit.call_count == int(not referenced)
        if referenced:
            assert response.json() == {"detail": "Tag is used by articles"}
        else:
            assert response.content == b""


@pytest.mark.parametrize("missing", [False, True])
def test_tag_detail_returns_the_current_record_or_the_domain_missing_identity(monkeypatch, missing):
    current = tag()
    read = Mock(
        return_value=current, side_effect=RecordNotFound("Tag not found") if missing else None
    )
    monkeypatch.setattr(taxonomy, "record", read)
    with operator_api(taxonomy) as api:
        response = api.client.get(f"/v1/admin/tags/{current.id}")
        assert response.status_code == (404 if missing else 200)
        if not missing:
            assert (
                response.json()["slug"] == "routing" and response.json()["auto_link_topic"] is False
            )
        read.assert_called_once_with(api.session, Tag, current.id)
        api.session.commit.assert_not_called()


def test_topic_create_keeps_its_explicit_editorial_status_and_returns_the_saved_identity(
    monkeypatch,
):
    current = topic()
    command = Mock(return_value=current)
    monkeypatch.setattr(topics, "save_topic", command)
    with operator_api(topics) as api:
        response = api.client.post(
            "/v1/admin/topics",
            json={
                "name": current.name,
                "slug": current.slug,
                "kind": "technology",
                "status": "active",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(current.id) and response.json()["status"] == "active"
        parsed = command.call_args.args[1]
        assert parsed.status == "active" and parsed.slug == current.slug
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("operation", ["read", "delete"])
def test_relationship_identity_uses_the_full_primary_key_and_missing_reads_never_delete(
    missing, operation
):
    focal, related = topic("React"), topic("JavaScript")
    relation = TopicRelation(
        topic_id=focal.id,
        related_topic_id=related.id,
        relation="uses_language",
        evidence_url="https://react.dev/learn",
    )
    with operator_api(topics) as api:
        api.session.get.return_value = None if missing else relation
        path = f"/v1/admin/topic-relations/{focal.id}/{related.id}/uses_language"
        response = api.client.delete(path) if operation == "delete" else api.client.get(path)
        assert response.status_code == (404 if missing else 204 if operation == "delete" else 200)
        api.session.get.assert_called_once_with(
            TopicRelation, (focal.id, related.id, "uses_language")
        )
        if not missing and operation == "delete":
            api.session.delete.assert_called_once_with(relation)
            api.session.commit.assert_called_once_with()
        else:
            api.session.delete.assert_not_called()
            api.session.commit.assert_not_called()
        if not missing and operation == "read":
            assert response.json() == {
                "topic_id": str(focal.id),
                "related_topic_id": str(related.id),
                "relation": "uses_language",
                "evidence_url": "https://react.dev/learn",
            }
