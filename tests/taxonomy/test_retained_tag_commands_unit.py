"""Retained labels compose attachment commands; SQL eligibility and rollback need integration."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_aggregator import tag_backfill
from devfeed_core.models import ArticleOrigin


@pytest.mark.parametrize("limit", [0, 501])
def test_backfill_rejects_an_unbounded_request_before_opening_storage(monkeypatch, limit):
    factory = Mock()
    monkeypatch.setattr(tag_backfill, "session_factory", factory)
    with pytest.raises(ValueError, match="between 1 and 500"):
        tag_backfill.backfill_tags(limit=limit)
    factory.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_backfill_rechecks_current_source_authority_and_groups_labels_by_article(
    monkeypatch, dry_run, selected
):
    first_source, revoked_source, article_id = (uuid.uuid4() for _ in range(3))
    rows = (
        [
            ArticleOrigin(
                id=uuid.UUID(int=1),
                article_id=article_id,
                source_id=first_source,
                entry_key="first",
                original_url="https://publisher.example/shared?utm_source=first",
                source_metadata={"tags": ["Python", "PYTHON", "<b>APIs</b>"]},
            ),
            ArticleOrigin(
                id=uuid.UUID(int=2),
                article_id=article_id,
                source_id=first_source,
                entry_key="second",
                original_url="https://publisher.example/shared?utm_source=second",
                source_metadata={"tags": ["Databases"]},
            ),
            ArticleOrigin(
                id=uuid.UUID(int=3),
                article_id=article_id,
                source_id=revoked_source,
                entry_key="third",
                original_url="https://publisher.example/shared?utm_source=other-feed",
                source_metadata={"tags": ["Revoked label"]},
            ),
        ]
        if selected
        else []
    )
    session, factory = MagicMock(), MagicMock()
    factory.return_value.__enter__.return_value = session
    monkeypatch.setattr(tag_backfill, "session_factory", lambda: factory)
    session.execute.return_value.all.return_value = rows
    # Rows selected before source review are not authority. Supply the subsequent
    # locked repository read separately; no SQL predicates are evaluated here.
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [first_source] if selected else []),
        iter([article_id] if selected else []),
    ]
    ids = {name: uuid.uuid4() for name in ("python", "apis", "databases")}
    resolved_names = []

    def resolve(_session, names):
        resolved_names.extend(names)
        return (ids, 2) if selected else ({}, 0)

    resolve_call, attach = Mock(side_effect=resolve), Mock(return_value=3)
    monkeypatch.setattr(tag_backfill, "resolve_source_tags", resolve_call)
    monkeypatch.setattr(tag_backfill, "attach_source_tags", attach)
    result = tag_backfill.backfill_tags(
        limit=3,
        after=uuid.UUID(int=0),
        source_id=first_source if not selected else None,
        dry_run=dry_run,
    )
    assert result == {
        "origins_examined": len(rows),
        "tags_created": 2 if selected else 0,
        "links_saved": 3 if selected else 0,
        "next_after": str(rows[-1].id) if selected else None,
        "dry_run": dry_run,
    }
    assert resolved_names == (["Python", "APIs", "Databases"] if selected else [])
    if selected:
        args = attach.call_args.args
        assert args[:2] == (session, article_id)
        assert list(args[2]) == list(ids.values())
    else:
        attach.assert_not_called()
    (session.rollback if dry_run else session.commit).assert_called_once_with()
    (session.commit if dry_run else session.rollback).assert_not_called()
