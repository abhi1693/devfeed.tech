"""Discovery commands retain provenance and distinguish candidate work from approval.

Insert/select outcomes are prepared repository results. These tests verify
orchestration, projection and parameters, without emulating conflict handling,
query filtering, locking or atomic deletion.
"""

import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import discovery
from devfeed_core.discovery_import import publisher_hint
from devfeed_core.models import CandidateDiscovery, CandidateFeed, SourceCandidate
from devfeed_core.services import OperationConflict, RecordNotFound
from test_source_readiness_unit import NOW, source


def candidate(**changes):
    return SourceCandidate(
        **{
            "id": uuid.uuid4(),
            "identity_url": "https://publisher.example/engineering",
            "name": "Engineering publisher",
            "status": "pending",
            "approval_status": "pending",
            "review": {},
            "created_at": NOW - timedelta(days=1),
            "updated_at": NOW,
            **changes,
        }
    )


@pytest.fixture
def repository(monkeypatch):
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    factory.return_value.__enter__.return_value = session
    monkeypatch.setattr(discovery, "session_factory", lambda: factory)
    return SimpleNamespace(session=session, factory=factory)


@pytest.mark.parametrize("origin", [" ", "x" * 2049])
def test_import_requires_bounded_provenance_before_opening_a_transaction(repository, origin):
    with pytest.raises(ValueError, match="Provenance"):
        discovery.import_publishers([], origin)
    repository.factory.begin.assert_not_called()


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("assess_after", [False, True])
def test_import_preserves_hint_provenance_and_deduplicates_reported_candidate_identity(
    repository,
    monkeypatch,
    background,
    assess_after,
):
    v = repository
    row = candidate()
    # Database reports one new insert followed by one existing identity. The
    # unit verifies the orchestration for those outcomes, not ON CONFLICT itself.
    v.session.scalar.side_effect = [row.id, row, None, row]
    hint = publisher_hint(row.identity_url, row.name, "https://publisher.example/feed")
    seed_id, checksum = uuid.uuid4(), hashlib.sha256(b"publisher-list").hexdigest()
    result = discovery.import_publishers(
        [hint, hint],
        "operator-list",
        seed_id=seed_id,
        checksum=checksum,
        background=background,
        assess_after=assess_after,
    )
    assert result == {"created": 1, "candidates": [str(row.id)]}
    [created_source] = [call.args[0] for call in v.session.add.call_args_list]
    assert created_source.id == row.id and row.source_id == row.id
    assert created_source.approval_status == "pending" and not created_source.enabled
    assert created_source.website_url == row.identity_url
    provenance = [
        call.args[0].compile().params
        for call in v.session.execute.call_args_list
        if call.args[0].table.name == "candidate_discoveries"
    ]
    expected_key = hashlib.sha256(b"operator-list\nhttps://publisher.example/feed").hexdigest()
    assert len(provenance) == 2
    assert all(
        {
            key: item[key]
            for key in ("candidate_id", "origin", "origin_key", "feed_hint", "seed_id", "checksum")
        }
        == {
            "candidate_id": row.id,
            "origin": "operator-list",
            "origin_key": expected_key,
            "feed_hint": hint.feed_hint,
            "seed_id": seed_id,
            "checksum": checksum,
        }
        for item in provenance
    )
    jobs = [
        call.args[0].compile().params
        for call in v.session.execute.call_args_list
        if call.args[0].table.name == "source_discovery_jobs" and call.args[0].is_insert
    ]
    assert [{key: item[key] for key in ("candidate_id", "stage", "result")} for item in jobs] == [
        {
            "candidate_id": row.id,
            "stage": "discover",
            "result": {
                "background": True,
                "assess_after": assess_after,
            }
            if background
            else {},
        }
    ]
    assert row.approval_status == "pending" and row.review == {}


def test_background_existing_ready_candidate_hands_off_without_repeating_discovery(
    repository, monkeypatch
):
    row = candidate(status="ready", selected_feed_id=uuid.uuid4(), source_id=uuid.uuid4())
    repository.session.scalar.side_effect = [None, row]
    handoff = Mock()
    monkeypatch.setattr(discovery, "handoff", handoff)
    result = discovery.import_publishers(
        [publisher_hint(row.identity_url)], "operator-list", background=True, assess_after=True
    )
    assert result == {"created": 0, "candidates": [str(row.id)]}
    handoff.assert_called_once_with(repository.session, row)
    assert row.approval_status == "pending"


@pytest.mark.parametrize("locked,attached", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("present", [False, True])
def test_candidate_lookup_reports_typed_absence_after_the_requested_repository_reads(
    locked, attached, present
):
    session, row = Mock(), candidate(source_id=uuid.uuid4() if attached else None)
    current_source = source(id=row.source_id) if attached else None
    reads = ([row.source_id, current_source] if attached else [None]) if locked else []
    session.scalar.side_effect = [*reads, row if present else None]
    if present:
        assert discovery.candidate(session, row.id, lock=locked) is row
    else:
        with pytest.raises(RecordNotFound, match="candidate not found"):
            discovery.candidate(session, row.id, lock=locked)
    session.add.assert_not_called()
    session.delete.assert_not_called()


def test_show_composes_source_authority_and_candidate_provenance_without_changing_history(
    repository,
):
    v = repository
    current = source(approval="approved", feed_url="https://publisher.example/feed")
    row = candidate(source_id=current.id, status="linked")
    feed = CandidateFeed(
        id=uuid.uuid4(),
        candidate_id=row.id,
        url="https://publisher.example/feed",
        evidence={"entries": 3},
        checked_at=NOW - timedelta(minutes=1),
    )
    row.selected_feed_id = feed.id
    item = CandidateDiscovery(
        id=uuid.uuid4(),
        candidate_id=row.id,
        origin="operator-list",
        origin_key=hashlib.sha256(b"operator-list\n").hexdigest(),
        created_at=NOW - timedelta(hours=1),
    )
    v.session.scalar.return_value = row
    v.session.get.return_value = current
    v.session.scalars.side_effect = [[item], [feed], [], []]
    output = discovery.show(row.id)
    assert output["id"] == row.id and output["status"] == "linked"
    assert output["approval_status"] == "approved"
    assert output["provenance"][0]["origin"] == "operator-list"
    assert output["feeds"][0]["candidate_id"] == row.id
    assert output["assessments"] == output["jobs"] == []
    assert row.approval_status == "pending"
    v.session.add.assert_not_called()


@pytest.mark.parametrize("filtered", [False, True])
def test_candidate_list_serializes_already_selected_rows_and_keeps_page_order(repository, filtered):
    rows = [
        candidate(name="Earlier publisher", created_at=NOW - timedelta(days=2)),
        candidate(name="Later publisher"),
    ]
    repository.session.scalars.return_value = rows
    output = discovery.list_candidates("pending" if filtered else None, limit=2, offset=3)
    assert [item["id"] for item in output] == [row.id for row in rows]
    assert [item["name"] for item in output] == ["Earlier publisher", "Later publisher"]
    assert all(item["approval_status"] == "pending" for item in output)


@pytest.mark.parametrize("running", [False, True])
def test_explicit_delete_validates_all_unique_candidates_before_any_delete_statement(
    repository, monkeypatch, running
):
    identifiers = [uuid.UUID(int=9), uuid.UUID(int=2)]
    validate = Mock(side_effect=lambda _session, identifier, **_: candidate(id=identifier))
    monkeypatch.setattr(discovery, "candidate", validate)
    repository.session.scalar.side_effect = [None, uuid.uuid4() if running else None]
    if running:
        with pytest.raises(OperationConflict, match="job is running"):
            discovery.delete_candidates([*identifiers, identifiers[0]])
        repository.session.execute.assert_not_called()
    else:
        assert discovery.delete_candidates([*identifiers, identifiers[0]]) == {"deleted": 2}
        statements = [call.args[0] for call in repository.session.execute.call_args_list]
        assert [statement.table.name for statement in statements] == [
            "candidate_assessments",
            "source_candidates",
        ]
        assert all(
            statement.compile().params[
                "candidate_id_1" if statement.table.name == "candidate_assessments" else "id_1"
            ]
            == sorted(identifiers)
            for statement in statements
        )
    assert [call.args[1] for call in validate.call_args_list] == sorted(identifiers)
    assert all(call.kwargs == {"lock": True} for call in validate.call_args_list)
