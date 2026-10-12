import importlib.util
import json
from pathlib import Path

from devfeed_core.partner_ad_formats import AUTOMATIC_AD_FORMATS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_partner_openapi", ROOT / "scripts/export_partner_openapi.py"
)
assert spec is not None and spec.loader is not None
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_partner_contract_export_is_current_and_needs_no_service_connections(monkeypatch):
    monkeypatch.setenv(
        "DEVFEED_DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1:1/contracts"
    )
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://127.0.0.1:1/0")

    def reject_connection(*args, **kwargs):
        raise AssertionError("Contract export attempted a network connection")

    monkeypatch.setattr("socket.socket.connect", reject_connection)
    document = exporter.build_contract()
    snapshot = json.loads((ROOT / "apps/partner/openapi.json").read_text())
    assert document == snapshot
    assert exporter.build_contract() == document
    assert document["x-devfeed-automatic-ad-formats"] == list(AUTOMATIC_AD_FORMATS)
    assert not any("/admin/" in path or "/user/" in path for path in document["paths"])


def test_authoritative_snapshot_keeps_input_and_output_models_distinct():
    document = exporter.build_contract()
    schemas = document["components"]["schemas"]
    assert "id" in schemas["AccountOut"]["required"]
    assert "allowed_promotion_kinds" in schemas["AccountOut"]["required"]
    assert "id" not in schemas["PartnerPreferencesInput"]["properties"]
    # Defaulted API fields stay optional in the authoritative OpenAPI. Only the
    # runtime response projection requires the complete serialized response.
    assert "outbound_destination" not in schemas["AccountOut"]["required"]
    assert schemas["AccountOut"]["properties"]["outbound_destination"]["default"] == "product_url"
