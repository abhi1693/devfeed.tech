"""Concurrent card lookup indexes resume safely after an interrupted build."""

import importlib.util
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support import paths


@pytest.fixture
def migration(monkeypatch):
    path = paths.REPO_ROOT / "migrations/versions/0039_card_reconciliation_indexes.py"
    spec = importlib.util.spec_from_file_location("card_reconciliation_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(index=None)
    context = SimpleNamespace(as_sql=False, autocommit_block=Mock(side_effect=nullcontext))
    result = SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: state.index))
    execute = Mock(return_value=result)
    create, drop = Mock(), Mock()
    monkeypatch.setattr(module.op, "get_context", lambda: context)
    monkeypatch.setattr(module.op, "get_bind", lambda: SimpleNamespace(execute=execute))
    monkeypatch.setattr(module.op, "create_index", create)
    monkeypatch.setattr(module.op, "drop_index", drop)
    return SimpleNamespace(
        module=module, state=state, context=context, execute=execute, create=create, drop=drop
    )


@pytest.mark.parametrize("valid", [False, True])
def test_interrupted_owned_index_is_rebuilt_without_touching_a_valid_index(migration, valid):
    name, table, columns = migration.module.INDEXES[0]
    migration.state.index = {
        "table_name": table,
        "columns": columns,
        "indisvalid": valid,
        "indisunique": False,
        "partial": False,
        "method": "btree",
        "attributes": 2,
        "expressions": False,
    }
    migration.module._repair_interrupted_index(name, table, columns)
    assert migration.execute.call_args.args[1] == {"name": name}
    if valid:
        migration.drop.assert_not_called()
    else:
        migration.drop.assert_called_once_with(
            name, table_name=table, postgresql_concurrently=True, if_exists=True
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"table_name": "unrelated"},
        {"columns": ["id"]},
        {"indisunique": True},
        {"partial": True},
        {"method": "brin"},
        {"attributes": 3},
        {"expressions": True},
    ],
)
def test_unexpected_same_name_index_is_preserved_and_rejected(migration, changed):
    name, table, columns = migration.module.INDEXES[0]
    migration.state.index = {
        "table_name": table,
        "columns": columns,
        "indisvalid": False,
        "indisunique": False,
        "partial": False,
        "method": "btree",
        "attributes": 2,
        "expressions": False,
    } | changed
    with pytest.raises(RuntimeError, match="Unexpected definition"):
        migration.module._repair_interrupted_index(name, table, columns)
    migration.drop.assert_not_called()
    migration.create.assert_not_called()


@pytest.mark.parametrize("offline", [False, True])
def test_upgrade_uses_concurrent_idempotent_indexes_outside_the_transaction(migration, offline):
    migration.context.as_sql = offline
    migration.module.upgrade()
    migration.context.autocommit_block.assert_called_once()
    assert migration.create.call_count == 2
    for call, (name, table, columns) in zip(
        migration.create.call_args_list, migration.module.INDEXES, strict=True
    ):
        assert call.args == (name, table, columns)
        assert call.kwargs == {"postgresql_concurrently": True, "if_not_exists": True}
    migration.drop.assert_not_called()
    assert migration.execute.call_count == (0 if offline else 2)


def test_downgrade_removes_only_the_two_owned_indexes_concurrently(migration):
    migration.module.downgrade()
    migration.context.autocommit_block.assert_called_once()
    assert migration.drop.call_count == 2
    for call, (name, table, _columns) in zip(
        migration.drop.call_args_list, reversed(migration.module.INDEXES), strict=True
    ):
        assert call.args == (name,)
        assert call.kwargs == {
            "table_name": table,
            "postgresql_concurrently": True,
            "if_exists": True,
        }
    migration.create.assert_not_called()
