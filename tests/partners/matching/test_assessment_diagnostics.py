"""Regression coverage for bounded durable qualification diagnostics."""

import copy
import uuid

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import DEFAULT_RETRY
from devfeed_core.models import PartnerPipelineJob, PartnerProduct
from devfeed_core.partner_connections import (
    QUALIFICATION_FAILURES,
    QualificationError,
    validate_qualification,
)

from tests.partners.catalog.test_tools import product_payload as shared_product_payload
from tests.partners.catalog.test_tools_integration import job_id, synced

product_payload = shared_product_payload
PAGE = {
    "final_url": "https://checker.example/",
    "text": "Detect breaking changes between OpenAPI specifications.",
}
SECRET = "private-response-credential-123"
CODES = tuple(QUALIFICATION_FAILURES)


def response(code=None):
    answer = {
        "decision": "qualified",
        "reason": "Checks API compatibility in CI.",
        "technologies": ["OpenAPI"],
        "evidence": [
            {"url": PAGE["final_url"], "quote": PAGE["text"], "capability": "Compare API schemas."}
        ],
    }
    if code == "invalid_output":
        return {"decision": SECRET}
    if code == "missing_capability_evidence":
        answer["evidence"] = []
    elif code == "evidence_url_mismatch":
        answer["evidence"][0]["url"] = f"https://{SECRET}.example/"
    elif code == "evidence_quote_mismatch":
        answer["evidence"][0]["quote"] = SECRET
    elif code == "generic_technology":
        answer["technologies"] = ["developer tools"]
    elif code == "technology_not_in_evidence":
        answer["technologies"] = [SECRET]
    return answer


@pytest.mark.parametrize("code", CODES)
def test_validation_failures_have_fixed_safe_messages(code):
    with pytest.raises(QualificationError) as error:
        validate_qualification(response(code), PAGE)
    assert error.value.code == code
    assert str(error.value) == QUALIFICATION_FAILURES[code]
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("decision", ["irrelevant", "uncertain"])
def test_nonqualified_assessments_keep_existing_evidence_clearing(decision):
    raw = response()
    raw["decision"] = decision
    result = validate_qualification(raw, PAGE)
    assert result.decision == decision
    assert result.technologies == result.evidence == []


@pytest.mark.integration
@pytest.mark.parametrize("code", [*CODES, "unexpected", "website_failure"])
def test_durable_assessment_persists_safe_reason_and_correlated_log(
    admin_client, database, product_payload, monkeypatch, caplog, code
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = synced(admin_client, database, product_payload)
    identifier = job_id(database, "assess")
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        job.attempts = DEFAULT_RETRY.max_attempts - 1

    page = {**PAGE, "final_url": product["product_url"]}

    def assess(*_):
        if code == "unexpected":
            raise RuntimeError(SECRET)
        raw = copy.deepcopy(response(code))
        if code != "invalid_output" and code != "evidence_url_mismatch" and raw["evidence"]:
            raw["evidence"][0]["url"] = page["final_url"]
        return raw

    def fetch(*_):
        if code == "website_failure":
            from devfeed_core.feeds.fetcher import FeedError

            raise FeedError(SECRET, status=503, retryable=True)
        return page

    process_pipeline(identifier, factory=database, page_fetcher=fetch, assessor=assess)
    diagnostic = code if code in CODES else "assessment_failed"
    with database() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        saved_product = session.get(PartnerProduct, uuid.UUID(product["id"]))
        assert job.status == "failed"
        assert job.payload["diagnostic"] == {
            "code": diagnostic,
            "stage": (
                "website_fetch"
                if code == "website_failure"
                else "model_inference"
                if code == "unexpected"
                else "qualification_validation"
            ),
        }
        expected = QUALIFICATION_FAILURES.get(code)
        if expected:
            assert job.error == expected
        assert saved_product.assessment == {"state": "attention", "reason": job.error}
        assert not saved_product.assessment.get("technologies")
        assert SECRET not in job.error
    failure = next(r for r in caplog.records if r.message == "partner_pipeline_failed")
    assert failure.diagnostic_code == diagnostic
    assert failure.job_id == identifier and failure.product_id == product["id"]
    assert failure.attempt > 0
    assert SECRET not in str(failure.__dict__)
    # Existing account/admin product contract carries the safe reason, without a schema change.
    shown = admin_client.get("/v1/admin/partner-tools").json()["items"][0]
    assert shown["assessment"]["reason"] == job.error
    assert SECRET not in str(shown)


@pytest.mark.integration
def test_invalid_evidence_keeps_retry_policy_and_success_clears_diagnostic(
    admin_client, database, product_payload, monkeypatch
):
    from datetime import timedelta

    from devfeed_core.models import utcnow

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = synced(admin_client, database, product_payload)
    identifier = job_id(database, "assess")
    page = {**PAGE, "final_url": product["product_url"]}
    process_pipeline(
        identifier,
        factory=database,
        page_fetcher=lambda *_: page,
        assessor=lambda *_: response("missing_capability_evidence"),
    )
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        assert job.status == "queued" and job.attempts == 1
        assert job.error == QUALIFICATION_FAILURES["missing_capability_evidence"]
        assert job.payload["diagnostic"]["code"] == "missing_capability_evidence"
        assert session.get(PartnerProduct, uuid.UUID(product["id"])).status == "pending"
        job.available_at = utcnow() - timedelta(seconds=1)
    raw = response()
    raw["evidence"][0]["url"] = page["final_url"]
    process_pipeline(
        identifier,
        factory=database,
        page_fetcher=lambda *_: page,
        assessor=lambda *_: raw,
    )
    with database() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        product = session.get(PartnerProduct, uuid.UUID(product["id"]))
        assert job.status == "succeeded" and job.error is None
        assert "diagnostic" not in job.payload
        assert product.assessment["state"] == "qualified"
        assert product.status == "approved"
