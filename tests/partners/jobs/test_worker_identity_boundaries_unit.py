"""Partner worker adapters keep redirect evidence bounded and execution scoped."""

import uuid
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from devfeed_aggregator import codex_client, partner_product_sync, partner_sync_tasks, partner_tasks
from devfeed_core.models import PartnerProductURL
from devfeed_core.partner_connections import Qualification
from devfeed_core.partner_connectors import ConnectorDiagnostic
from devfeed_core.partner_tools import EvaluationResult, ProductInput, product_snapshot
from devfeed_core.urls import fingerprint
from devfeed_http.partner_connector_previews import public_diagnostic
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tests.admin.partners.test_partner_read_contracts_unit import evaluation
from tests.partners.catalog.test_catalog_commands_unit import listing
from tests.support.partner_matching import article, product
from tests.support.source_readiness import configuration


class Reads:
    def __init__(self, session):
        self.session, self.open = session, False

    @contextmanager
    def __call__(self):
        self.open = True
        try:
            yield self.session
        finally:
            self.open = False


def incoming(value, **changes):
    return ProductInput(
        **{
            "provider": "nick-launches",
            "external_id": str(value.id),
            "name": value.name,
            "product_url": value.product_url.replace("http://www.", "https://"),
            "listing_url": f"https://nicklaunches.com/products/{value.id}",
            "tagline": value.tagline,
            "pricing": value.pricing,
            **changes,
        }
    )


def test_redirect_observation_is_bounded_to_ten_candidates_and_runs_outside_read_scope():
    products = [
        product(name=f"API Checker {index}", product_url=f"http://www.checker.example/tool-{index}")
        for index in range(12)
    ]
    items = [incoming(value) for value in products]
    sources = [
        listing(value, external_id=item.external_id)
        for value, item in zip(products, items, strict=True)
    ]
    session = Mock(spec=Session)
    session.scalar.side_effect = sources
    session.get.side_effect = lambda model, key: (
        None if model is PartnerProductURL else next(value for value in products if value.id == key)
    )
    factory = Reads(session)
    targets = {
        value.product_url: item.product_url for value, item in zip(products, items, strict=True)
    }

    def fetch(url, timeout):
        assert not factory.open and timeout == 5
        return {"final_url": targets[url]}

    fetcher = Mock(side_effect=fetch)
    proofs = partner_sync_tasks.pending_redirect_proofs(factory, items, fetcher)
    assert proofs == [
        (value.id, value.revision, item.product_url)
        for value, item in zip(products[:10], items[:10], strict=True)
    ]
    assert fetcher.call_count == 10
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "new_listing",
        "verified_alias",
        "product_removed",
        "product_merged",
        "different_final_url",
        "fetch_error",
    ],
)
def test_redirect_observation_never_invents_proof_when_identity_changed_or_network_failed(case):
    value = product(product_url="http://www.checker.example/")
    item, source = incoming(value), listing(value)
    session = Mock(spec=Session)
    session.scalar.return_value = None if case == "new_listing" else source
    alias = PartnerProductURL(
        url_hash=fingerprint(item.product_url), url=item.product_url, product_id=value.id
    )
    if case == "product_merged":
        value.merged_into_id, value.status, value.verified_at = uuid.uuid4(), "withdrawn", None
    # A nonlocking listing read may precede a committed removal or merge; its
    # subsequent product read must refuse those now obsolete observations.
    session.get.side_effect = lambda model, _key: (
        (alias if case == "verified_alias" else None)
        if model is PartnerProductURL
        else None
        if case == "product_removed"
        else value
    )
    fetcher = Mock(return_value={"final_url": "https://checker.example/different-path"})
    if case == "fetch_error":
        fetcher.side_effect = RuntimeError("Private upstream diagnostic")
    assert partner_sync_tasks.pending_redirect_proofs(Reads(session), [item], fetcher) == []
    assert fetcher.call_count == (1 if case in {"fetch_error", "different_final_url"} else 0)


@pytest.mark.parametrize("operation", ["sync_product", "assess"])
def test_public_pipeline_entrypoint_routes_one_uuid_and_preserves_injected_io_boundaries(
    monkeypatch, operation
):
    identifier, session = uuid.uuid4(), Mock(spec=Session)
    session.scalar.return_value = operation
    factory = Reads(session)
    child, pipeline = Mock(return_value="child-result"), Mock(return_value="pipeline-result")
    monkeypatch.setattr(partner_product_sync, "process_product_sync", child)
    monkeypatch.setattr(partner_sync_tasks, "_process_pipeline", pipeline)
    page, reader, assessor, product_reader = Mock(), Mock(), Mock(), Mock()
    output = partner_sync_tasks.process_pipeline(
        str(identifier),
        factory=factory,
        reader=reader,
        page_fetcher=page,
        assessor=assessor,
        product_reader=product_reader,
    )
    assert not factory.open
    if operation == "sync_product":
        assert output == "child-result"
        child.assert_called_once_with(
            identifier, factory, product_reader=product_reader, page_fetcher=page
        )
        pipeline.assert_not_called()
    else:
        assert output == "pipeline-result"
        pipeline.assert_called_once_with(
            str(identifier), factory=factory, reader=reader, page_fetcher=page, assessor=assessor
        )
        child.assert_not_called()


def test_runtime_dispatch_failure_preserves_cause_and_logs_only_diagnostic_type(
    monkeypatch, caplog
):
    identifier, session = uuid.uuid4(), Mock(spec=Session)
    session.scalar.side_effect = RuntimeError("private credential from database")
    with (
        caplog.at_level("ERROR", logger=partner_sync_tasks.__name__),
        pytest.raises(RuntimeError, match="private credential"),
    ):
        partner_sync_tasks.process_pipeline(str(identifier), factory=Reads(session))
    [record] = [record for record in caplog.records if record.name == partner_sync_tasks.__name__]
    assert record.error_type == "RuntimeError" and "private credential" not in record.getMessage()


@pytest.mark.parametrize("lane", ["qualification", "evaluation"])
def test_partner_ai_provider_receives_actual_schema_and_factual_snapshot(monkeypatch, lane):
    settings, client, value = configuration(ai_enabled=True), Mock(), product()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(codex_client, "CodexClient", constructor)
    if lane == "qualification":
        monkeypatch.setattr(partner_sync_tasks, "get_settings", lambda: settings)
        page = {
            "url": value.product_url,
            "final_url": value.product_url,
            "text": value.evidence[0]["quote"],
        }
        output = partner_sync_tasks.qualify(product_snapshot(value), page)
        assert client.operation == "partner_qualification"
        assert client.complete.call_args.args[1] == Qualification.model_json_schema()
    else:
        monkeypatch.setattr(partner_tasks, "get_settings", lambda: settings)
        saved = evaluation(value, article())
        output = partner_tasks.assess(saved.snapshot)
        assert client.operation == "partner_tool_evaluation"
        assert client.complete.call_args.args[1] == EvaluationResult.model_json_schema()
    assert output is client.complete.return_value
    assert value.evidence[0]["quote"] in client.complete.call_args.args[0]
    constructor.assert_called_once_with(settings)


@pytest.mark.parametrize("case", ["safe", "validation", "fallback"])
def test_preview_diagnostic_exposes_only_approved_message_or_field_names(case):
    if case == "safe":
        error = ConnectorDiagnostic("Product ID paths did not match a nonempty value.")
        assert public_diagnostic(error, "Import preview failed") == str(error)
    elif case == "validation":
        with pytest.raises(ValidationError) as invalid:
            ProductInput(
                provider="nick-launches",
                external_id="checker",
                name="",
                product_url="http://127.0.0.1/private",
                tagline="A valid product tagline.",
                listing_url="https://launch.example/checker",
            )
        message = public_diagnostic(invalid.value, "Import preview failed")
        assert "Product name" in message and "Website URL" in message
        assert "127.0.0.1" not in message
    else:
        assert (
            public_diagnostic(RuntimeError("private credential"), "Import preview failed")
            == "Import preview failed"
        )
