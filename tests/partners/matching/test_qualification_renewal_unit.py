"""Background qualification renewal must not withdraw a still-valid approval."""

import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import partner_connections
from devfeed_core.models import PartnerPipelineJob, utcnow


def approved_product(**changes):
    return SimpleNamespace(
        **{
            "id": uuid.uuid4(),
            "revision": 4,
            "assessment_revision": 4,
            "status": "approved",
            "verified_at": utcnow() - timedelta(days=84),
            "updated_at": utcnow() - timedelta(days=84),
            "assessment": {"state": "qualified", "reason": "Official Kubernetes support."},
            "technologies": ["Kubernetes"],
            "evidence": [
                {
                    "url": "https://monitor.example/",
                    "quote": "Monitor Kubernetes workloads.",
                }
            ],
            "excluded": False,
            "merged_into_id": None,
            **changes,
        }
    )


@pytest.fixture
def assessment_session(monkeypatch):
    session = Mock()
    session.scalar.return_value = None
    monkeypatch.setattr(partner_connections, "active_provider", lambda *_: None)
    monkeypatch.setattr(partner_connections, "product_has_active_source", lambda *_: True)
    return session


@pytest.mark.parametrize("provider", [None, "launch-platform"])
def test_renewal_queues_current_revision_without_invalidating_approved_facts(
    monkeypatch, assessment_session, provider
):
    product = approved_product()
    before = deepcopy(vars(product))
    monkeypatch.setattr(partner_connections, "active_provider", lambda *_: provider)

    partner_connections.request_assessment(assessment_session, product, renewal=True)

    assert vars(product) == before
    assessment_session.add.assert_called_once()
    job = assessment_session.add.call_args.args[0]
    assert isinstance(job, PartnerPipelineJob)
    assert job.operation == "assess"
    assert job.product_id == product.id
    assert job.provider == provider
    assert job.payload["revision"] == product.revision


def test_explicit_reassessment_still_invalidates_the_previous_approval(assessment_session):
    product = approved_product()
    before_evidence = deepcopy(product.evidence)
    before_updated_at = product.updated_at

    partner_connections.request_assessment(assessment_session, product)

    assert product.status == "pending"
    assert product.assessment_revision == 0
    assert product.verified_at is None
    assert product.assessment["state"] == "checking"
    assert product.evidence == before_evidence
    assert product.updated_at == before_updated_at
    assessment_session.add.assert_called_once()
    job = assessment_session.add.call_args.args[0]
    assert job.operation == "assess"
    assert job.product_id == product.id
    assert job.payload["revision"] == product.revision


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"assessment_revision": 3}, id="outdated-evidence"),
        pytest.param({"status": "pending"}, id="pending-product"),
        pytest.param({"status": "rejected"}, id="rejected-product"),
        pytest.param({"verified_at": None}, id="unverified-product"),
    ],
)
def test_renewal_does_not_preserve_an_invalid_approval(assessment_session, changes):
    product = approved_product(**changes)

    partner_connections.request_assessment(assessment_session, product, renewal=True)

    assert product.status == "pending"
    assert product.assessment_revision == 0
    assert product.verified_at is None
    assert product.assessment["state"] == "checking"
    assessment_session.add.assert_called_once()
    job = assessment_session.add.call_args.args[0]
    assert job.operation == "assess"
    assert job.product_id == product.id
    assert job.payload["revision"] == product.revision


@pytest.mark.parametrize("renewal", [False, True])
@pytest.mark.parametrize("guard", ["active-job", "excluded", "merged", "withdrawn", "no-source"])
def test_assessment_guards_preserve_product_and_do_not_enqueue(
    monkeypatch, assessment_session, renewal, guard
):
    product = approved_product(
        excluded=guard == "excluded",
        merged_into_id=uuid.uuid4() if guard == "merged" else None,
        status="withdrawn" if guard == "withdrawn" else "approved",
    )
    before = deepcopy(vars(product))
    if guard == "active-job":
        assessment_session.scalar.return_value = uuid.uuid4()
    monkeypatch.setattr(
        partner_connections, "product_has_active_source", lambda *_: guard != "no-source"
    )

    partner_connections.request_assessment(assessment_session, product, renewal=renewal)

    assert vars(product) == before
    assessment_session.add.assert_not_called()
