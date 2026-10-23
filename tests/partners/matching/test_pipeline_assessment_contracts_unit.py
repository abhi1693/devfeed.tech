"""Partner qualification applies only current, grounded results from the official page."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import codex_client
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_core import logos, partner_product_images, partner_promotions
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease, start_job
from devfeed_core.models import PartnerProduct

from tests.discovery.test_discovery_review_contracts_unit import NOW
from tests.partners.jobs.test_pipeline_sync_contracts_unit import pipeline_case as pipeline_case
from tests.partners.jobs.test_pipeline_sync_contracts_unit import process
from tests.support.source_readiness import configuration

QUOTE = "Detect breaking changes between OpenAPI specifications."


def result(url, decision="qualified"):
    return {
        "decision": decision,
        "reason": "The official page establishes OpenAPI compatibility checks for developers.",
        "technologies": ["OpenAPI"],
        "evidence": [{"url": url, "quote": QUOTE, "capability": "Check API compatibility in CI."}],
    }


@pytest.fixture
def assessment(pipeline_case, monkeypatch):
    value = pipeline_case
    value.job.operation, value.job.product_id = "assess", value.product.id
    value.job.payload = {"revision": value.product.revision}
    value.page = {"final_url": value.product.product_url, "text": QUOTE}
    value.fetch = Mock(return_value=value.page)
    value.assessor = Mock(return_value=result(value.product.product_url))
    value.redirect = Mock(return_value=value.product)
    monkeypatch.setattr(pipeline, "record_verified_redirect", value.redirect)
    value.evaluation, value.logo, value.image, value.assets, value.reassessment = (
        Mock() for _ in range(5)
    )
    monkeypatch.setattr(pipeline, "queue_evaluation", value.evaluation)
    monkeypatch.setattr(pipeline, "request_assessment", value.reassessment)
    monkeypatch.setattr(logos, "request_logo", value.logo)
    monkeypatch.setattr(partner_product_images, "request_product_image", value.image)
    monkeypatch.setattr(partner_promotions, "ensure_automatic_assets", value.assets)
    return value


@pytest.mark.parametrize(
    "decision,status,state",
    [
        ("qualified", "approved", "qualified"),
        ("irrelevant", "rejected", "irrelevant"),
        ("uncertain", "pending", "attention"),
    ],
)
def test_validated_decision_updates_current_revision_and_qualification_dependent_work(
    assessment, decision, status, state
):
    value = assessment
    value.job.payload["diagnostic"] = {"code": "assessment_failed", "stage": "website_fetch"}

    def fetch(url, timeout):
        assert not value.state.open and url == value.product.product_url and timeout == 15
        return value.page

    def assess(snapshot, page):
        assert not value.state.open
        assert (
            snapshot["revision"] == value.product.revision
            and snapshot["product_url"] == value.product.product_url
        )
        assert page == value.page
        return result(page["final_url"], decision)

    process(value, page_fetcher=fetch, assessor=assess)
    assert (value.product.status, value.product.assessment["state"]) == (status, state)
    assert value.product.assessment_revision == value.product.revision == 1
    assert value.product.verified_at == (NOW if decision == "qualified" else None)
    assert value.job.status == "succeeded" and value.job.finished_at == NOW
    assert value.job.lease_token is value.job.lease_until is None
    assert "diagnostic" not in value.job.payload
    assert value.product.technologies == (["OpenAPI"] if decision == "qualified" else [])
    assert len(value.product.evidence) == (1 if decision == "qualified" else 0)
    value.evaluation.assert_called_once_with(value.session, value.product)
    value.assets.assert_called_once_with(value.session, product_ids=[value.product.id])
    if decision == "qualified":
        value.logo.assert_called_once_with(
            value.session, PartnerProduct, value.product.id, automatic=True
        )
        value.image.assert_called_once_with(value.session, value.product.id, automatic=True)
        assert value.redirect.call_count == 2
    else:
        value.logo.assert_not_called()
        value.image.assert_not_called()


def test_page_passed_to_model_is_bounded_and_excludes_unneeded_fetch_metadata(assessment):
    value = assessment
    value.fetch.return_value = {
        "final_url": value.product.product_url,
        "text": "x" * 25000,
        "private_fetch_metadata": "not-model-input",
    }
    value.assessor.return_value = {
        "decision": "uncertain",
        "reason": "The page does not establish specific developer capabilities.",
        "technologies": [],
        "evidence": [],
    }
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    page = value.assessor.call_args.args[1]
    assert set(page) == {"final_url", "text"} and len(page["text"]) == 20000
    assert value.product.status == "pending" and value.product.assessment["state"] == "attention"


@pytest.mark.parametrize("barrier", ["revision", "excluded", "source_withdrawn", "ai_disabled"])
def test_stale_or_unavailable_assessment_does_not_fetch_or_spend_attempt(
    assessment, monkeypatch, barrier
):
    value = assessment
    if barrier == "revision":
        value.product.revision = 2
    elif barrier == "excluded":
        value.product.excluded = True
    elif barrier == "source_withdrawn":
        value.listing.active, value.product.status = False, "withdrawn"
        value.availability.return_value = False
    else:
        monkeypatch.setattr(pipeline, "get_settings", lambda: configuration(ai_enabled=False))
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    value.fetch.assert_not_called()
    value.assessor.assert_not_called()
    assert value.job.attempts == 0
    if barrier == "ai_disabled":
        assert value.job.status == "queued" and value.product.status == "pending"
    else:
        assert value.job.status == "failed" and value.job.lease_token is None
        assert value.job.error == (
            "Product source is unavailable" if barrier == "source_withdrawn" else "Product changed"
        )
    value.evaluation.assert_not_called()


@pytest.mark.parametrize("same_revision", [False, True])
def test_exhausted_assessment_marks_only_its_current_product_revision_for_operator_attention(
    assessment, same_revision
):
    value = assessment
    value.job.attempts = 3
    if not same_revision:
        value.product.revision += 1
    before = copy.deepcopy(value.product.assessment)
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    assert value.job.status == "failed" and value.job.attempts == 3
    assert value.job.error == "Automatic checks exhausted their retry limit"
    if same_revision:
        assert value.product.assessment_revision == value.product.revision
        assert value.product.assessment == {"state": "attention", "reason": value.job.error}
    else:
        assert value.product.assessment == before and value.product.assessment_revision == 0
    value.fetch.assert_not_called()


@pytest.mark.parametrize("change", ["revision", "source", "deleted", "lease"])
def test_fetch_result_is_rechecked_before_model_inference(assessment, change):
    value = assessment

    def fetch(*args):
        if change == "revision":
            value.product.revision += 1
        elif change == "source":
            value.listing.active, value.product.status = False, "withdrawn"
            value.availability.return_value = False
        elif change == "deleted":
            value.rows[PartnerProduct] = None
        else:
            value.state.now = NOW + timedelta(seconds=361)
            start_job(value.job, value.state.now, 360)
        return value.page

    process(value, page_fetcher=fetch, assessor=value.assessor)
    value.assessor.assert_not_called()
    value.evaluation.assert_not_called()
    if change == "lease":
        assert value.job.status == "running" and value.job.finished_at is None
    else:
        assert value.job.status == "failed" and value.job.error == "Product or connection changed"
        assert value.job.lease_token is None


@pytest.mark.parametrize("change", ["revision", "excluded", "source", "lease"])
def test_late_model_result_cannot_approve_changed_or_unowned_product(assessment, change):
    value = assessment

    def assess(*args):
        if change == "revision":
            value.product.revision += 1
        elif change == "excluded":
            value.product.excluded = True
        elif change == "source":
            value.listing.active, value.product.status = False, "withdrawn"
            value.availability.return_value = False
        else:
            value.state.now = NOW + timedelta(seconds=361)
            start_job(value.job, value.state.now, 360)
        return result(value.page["final_url"])

    process(value, page_fetcher=value.fetch, assessor=assess)
    assert value.product.status == ("withdrawn" if change == "source" else "pending")
    assert value.product.assessment_revision == 0 and value.product.verified_at is None
    value.evaluation.assert_not_called()
    value.logo.assert_not_called()
    if change == "lease":
        assert value.job.status == "running"
    else:
        assert value.job.status == "failed"
        assert value.job.error == (
            "Product source is unavailable" if change == "source" else "Product changed"
        )


@pytest.mark.parametrize("retryable,status", [(True, 503), (False, 404)])
def test_website_transport_failure_has_actionable_diagnostic_and_preserves_retry_budget(
    assessment, retryable, status, caplog
):
    value = assessment
    value.fetch.side_effect = FeedError(
        "signed-url?token=secret", status=status, retryable=retryable, retry_after=120
    )
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    assert value.job.error == f"Product website could not be read (HTTP {status})"
    assert value.job.payload["diagnostic"] == {
        "code": "assessment_failed",
        "stage": "website_fetch",
    }
    assert value.job.status == ("queued" if retryable else "failed")
    if retryable:
        assert value.job.available_at == NOW + timedelta(seconds=120)
        assert value.product.assessment_revision == 0
    else:
        assert value.product.assessment_revision == value.product.revision
        assert value.product.assessment == {"state": "attention", "reason": value.job.error}
    assert "secret" not in caplog.text
    value.assessor.assert_not_called()


@pytest.mark.parametrize("attempts,expected", [(0, "queued"), (2, "failed")])
def test_real_qualification_validator_rejects_invented_quotes_and_bounds_retries(
    assessment, attempts, expected
):
    value = assessment
    value.job.attempts = attempts
    value.assessor.return_value["evidence"][0]["quote"] = (
        "An invented capability absent from the fetched page."
    )
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    assert value.job.status == expected and value.product.status == "pending"
    assert value.job.payload["diagnostic"] == {
        "code": "evidence_quote_mismatch",
        "stage": "qualification_validation",
    }
    assert "invented capability" not in value.job.error
    assert value.product.technologies == [] and value.product.evidence == []
    value.logo.assert_not_called()


def test_unexpected_model_failure_records_safe_stage_without_provider_payload(assessment, caplog):
    value = assessment
    value.assessor.side_effect = RuntimeError("private prompt and provider token=secret")
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    assert (
        value.job.status == "queued"
        and value.job.error == "Product capabilities could not be verified"
    )
    assert value.job.payload["diagnostic"] == {
        "code": "assessment_failed",
        "stage": "model_inference",
    }
    assert "token=secret" not in caplog.text
    value.evaluation.assert_not_called()


@pytest.mark.parametrize("stage", ["before_model", "before_approval"])
def test_redirect_merge_stops_work_on_the_superseded_product(assessment, stage):
    value = assessment
    target = PartnerProduct(
        id=uuid.uuid4(),
        name="Canonical API Checker",
        product_url="https://canonical.example/",
        tagline="Check API compatibility in CI.",
        status="pending",
        revision=1,
    )
    calls = []

    def resolve(session, product, final_url):
        calls.append(final_url)
        if stage == "before_approval" and len(calls) == 1:
            return product
        # Supply the real redirect service's post-merge outcome; do not emulate its SQL.
        product.status, product.merged_into_id = "withdrawn", target.id
        value.listing.product_id = target.id
        value.job.status, value.job.error, value.job.finished_at = "failed", "Product merged", NOW
        clear_lease(value.job)
        return target

    value.redirect.side_effect = resolve
    process(value, page_fetcher=value.fetch, assessor=value.assessor)
    assert value.product.status == "withdrawn" and value.product.merged_into_id == target.id
    assert value.product.assessment_revision == 0 and value.job.status == "failed"
    assert value.assessor.call_count == (0 if stage == "before_model" else 1)
    value.evaluation.assert_not_called()
    value.logo.assert_not_called()


def test_default_qualification_adapter_uses_bounded_domain_prompt_and_schema(
    assessment, monkeypatch
):
    value = assessment
    client = SimpleNamespace(complete=Mock(return_value=result(value.product.product_url)))
    monkeypatch.setattr(codex_client, "CodexClient", lambda _: client)
    process(value, page_fetcher=value.fetch)
    assert client.operation == "partner_qualification" and value.product.status == "approved"
    prompt, schema = client.complete.call_args.args
    assert value.product.product_url in prompt and QUOTE in prompt
    assert schema["properties"]["decision"]["enum"] == ["qualified", "irrelevant", "uncertain"]
