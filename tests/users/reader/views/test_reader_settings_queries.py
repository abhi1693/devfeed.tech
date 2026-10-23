"""Reader preference queries project their owned snapshot once, including under a write lock."""

from contextlib import contextmanager

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import UserAccount
from devfeed_core.user_settings import FeedSettings
from sqlalchemy import event

from tests.users.reader.views.test_column_starters import starter_data as starter_data

pytestmark = pytest.mark.integration
ROOT = "/v1/user/settings"


@contextmanager
def queries():
    statements = []

    def counted(conn, cursor, statement, parameters, context, many):
        statements.append(" ".join(statement.split()))

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", counted)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", counted)


def assert_owned_snapshot(statement, *, locked):
    assert statement.startswith("SELECT user_accounts.id, user_accounts.feed_settings FROM")
    for field in ("id", "issuer", "subject", "organization_id"):
        assert f"user_accounts.{field} = " in statement
    assert ("FOR UPDATE" in statement) is locked


@pytest.mark.parametrize(
    "path, budget",
    [
        ("/feed", 1),
        ("/feed-view?feed=my_feed", 1),
        ("/columns", 1),
        ("/columns?workspace=my_feed", 1),
        ("/columns/starters", 5),
    ],
)
def test_reader_settings_gets_project_only_the_owned_snapshot(starter_data, path, budget):
    client = starter_data[0]
    with queries() as statements:
        response = client.get(ROOT + path)
    assert response.status_code == 200
    assert len(statements) == budget
    assert_owned_snapshot(statements[0], locked=False)
    assert all(statement.startswith("SELECT ") for statement in statements)


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/feed", {"view": "compact"}),
        ("/feed-view?feed=my_feed", {"view": "columns"}),
        ("/columns", {"columns": []}),
        ("/columns?workspace=my_feed", {"columns": [], "mode": "initialize"}),
    ],
)
def test_reader_settings_puts_read_the_json_under_the_lock_once(starter_data, path, payload):
    client = starter_data[0]
    with queries() as statements:
        response = client.put(ROOT + path, json=payload)
    assert response.status_code == 200
    assert len(statements) == 2
    assert_owned_snapshot(statements[0], locked=True)
    assert statements[1].startswith("UPDATE user_accounts SET feed_settings=")
    assert client.get(ROOT + path).json() == response.json()


@pytest.mark.parametrize("workspace", ["latest", "my_feed"])
@pytest.mark.parametrize("mode", ["initialize", "restore_empty"])
def test_column_initialization_noops_use_only_the_locked_snapshot(starter_data, workspace, mode):
    client = starter_data[0]
    path = f"{ROOT}/columns?workspace={workspace}"
    columns = [{"id": "1cd602c8-ade0-4b19-8f61-448a679f5f45", "feed": {"kind": "latest"}}]
    saved = client.put(path, json={"columns": columns})
    assert saved.status_code == 200
    with queries() as statements:
        response = client.put(path, json={"columns": [], "mode": mode})
    assert response.status_code == 200
    assert response.json() == saved.json()
    assert len(statements) == 1
    assert_owned_snapshot(statements[0], locked=True)


def test_saved_json_null_recovers_without_treating_the_owned_account_as_missing(
    starter_data, database
):
    client, _, account, _ = starter_data
    with database.begin() as session:
        session.get(UserAccount, account).feed_settings = None
    expected = [
        ("/feed", FeedSettings().model_dump()),
        ("/feed-view?feed=my_feed", {"view": "cards"}),
        ("/columns", {"initialized": False, "columns": []}),
        ("/columns?workspace=my_feed", {"initialized": False, "columns": []}),
        ("/columns/starters", {"feeds": []}),
    ]
    with queries() as statements:
        for path, defaults in expected:
            response = client.get(ROOT + path)
            assert response.status_code == 200
            assert response.json() == defaults
    assert all(statement.startswith("SELECT ") for statement in statements)
    with database() as session:
        assert session.get(UserAccount, account).feed_settings is None
