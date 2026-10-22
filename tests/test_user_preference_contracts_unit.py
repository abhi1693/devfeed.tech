"""Preference HTTP commands normalize input and reject unavailable selections.

Repository reads are supplied projections. These cases do not evaluate SQL
ownership predicates, locks, uniqueness, or transaction isolation.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import dependencies, preferences
from devfeed_user_api.auth import UserIdentity, require_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert


class Rows(list):
    def all(self):
        return list(self)


@pytest.fixture
def api():
    owner = uuid.uuid4()
    identity = UserIdentity(
        user_id=str(owner),
        issuer="https://identity.example",
        subject="reader",
        organization_id="readers",
        expires_at=4_102_444_800,
        csrf_token="test-csrf",
    )
    session = Mock(spec=Session)
    session.scalar.return_value = owner
    app = FastAPI()
    app.include_router(preferences.router)
    app.dependency_overrides[require_user] = lambda: identity
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(owner=owner, identity=identity, session=session, client=client)


def bulk_path(kind):
    return "/v1/user/preferences" + ("" if kind == "topics" else f"/{kind}")


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
@pytest.mark.parametrize("empty", [False, True])
def test_bulk_preference_commands_sort_and_deduplicate_validated_selections(api, kind, empty):
    identifiers = [] if empty else [uuid.UUID(int=2), uuid.UUID(int=1)]
    api.session.scalars.return_value = Rows(identifiers)
    field = {"topics": "topic_ids", "sources": "source_ids", "tags": "tag_ids"}[kind]

    response = api.client.put(
        bulk_path(kind), json={field: [str(value) for value in identifiers + identifiers[:1]]}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {field: sorted(str(value) for value in identifiers)}
    commands = api.session.execute.call_args_list
    assert isinstance(commands[0].args[0], Delete)
    assert api.owner in commands[0].args[0].compile().params.values()
    if identifiers:
        assert isinstance(commands[1].args[0], Insert)
        values = commands[1].args[1]
        assert [row["user_id"] for row in values] == [api.owner] * 2
        assert [row[field[:-1]] for row in values] == sorted(identifiers)
    else:
        assert len(commands) == 1
    api.session.commit.assert_called_once()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
def test_bulk_preferences_reject_a_partially_available_selection_without_writing(api, kind):
    first, missing = uuid.uuid4(), uuid.uuid4()
    api.session.scalars.return_value = Rows([first])
    field = {"topics": "topic_ids", "sources": "source_ids", "tags": "tag_ids"}[kind]

    response = api.client.put(bulk_path(kind), json={field: [str(first), str(missing)]})

    assert response.status_code == 422
    api.session.execute.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
def test_preference_reads_return_only_the_supplied_catalog_ids(api, kind):
    identifiers = [uuid.uuid4(), uuid.uuid4()]
    api.session.scalars.return_value = Rows(identifiers)
    field = {"topics": "topic_ids", "sources": "source_ids", "tags": "tag_ids"}[kind]

    response = api.client.get(bulk_path(kind))

    assert response.status_code == 200 and response.json() == {
        field: [str(identifier) for identifier in identifiers]
    }
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
@pytest.mark.parametrize("followed", [False, True])
def test_single_follow_command_keeps_explicit_intent_and_uses_session_ownership(
    api, kind, followed
):
    identifier = uuid.uuid4()
    api.session.scalar.side_effect = [api.owner, identifier, 99]
    api.session.execute.return_value = SimpleNamespace(scalar_one=lambda: 99)

    response = api.client.put(
        f"{bulk_path(kind) if kind != 'topics' else '/v1/user/preferences/topics'}/{identifier}",
        json={"followed": followed},
    )

    assert response.status_code == 200 and response.json() == {"followed": followed}
    command = api.session.execute.call_args.args[0]
    assert isinstance(command, Insert if followed else Delete)
    parameters = command.compile().params
    assert api.owner in parameters.values() and identifier in parameters.values()
    api.session.commit.assert_called_once()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
@pytest.mark.parametrize("failure", ["unavailable", "limit"])
def test_follow_admission_requires_an_available_choice_and_room_below_the_limit(api, kind, failure):
    identifier = uuid.uuid4()
    api.session.scalar.side_effect = [
        api.owner,
        None if failure == "unavailable" else identifier,
        100,
    ]
    api.session.execute.return_value = SimpleNamespace(scalar_one=lambda: 100)
    path = "/v1/user/preferences/" + kind + "/" + str(identifier)

    response = api.client.put(path, json={"followed": True})

    assert response.status_code == 422
    if failure == "limit":
        assert "100" in response.json()["detail"]
    assert all(
        not isinstance(call.args[0], (Delete, Insert))
        for call in api.session.execute.call_args_list
    )
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
def test_mutations_reject_an_unavailable_account_before_catalog_or_write_access(api, kind):
    api.session.scalar.return_value = None

    response = api.client.put(
        f"/v1/user/preferences/{kind}/{uuid.uuid4()}", json={"followed": False}
    )

    assert response.status_code == 401
    api.session.execute.assert_not_called()
    api.session.scalars.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["topics", "sources", "tags"])
def test_preference_payload_cannot_select_a_different_owner(api, kind):
    field = {"topics": "topic_ids", "sources": "source_ids", "tags": "tag_ids"}[kind]

    response = api.client.put(bulk_path(kind), json={field: [], "user_id": str(uuid.uuid4())})

    assert response.status_code == 422
    api.session.scalar.assert_not_called()
    api.session.execute.assert_not_called()
