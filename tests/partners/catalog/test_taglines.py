"""Product taglines are short display copy, separate from source and capability evidence."""

from types import SimpleNamespace

import pytest
from devfeed_core.models import PartnerListing, PartnerProduct
from devfeed_core.partner_connectors import ConnectorClient, ConnectorConfig, ProductCandidate
from devfeed_core.partner_taglines import Tagline, imported_tagline
from devfeed_core.partner_tools import (
    ListingOut,
    ProductInput,
    ProductOut,
    ProductSnapshot,
    product_snapshot,
)
from devfeed_http.partner_placements import PlacementOut
from devfeed_http.partner_portal import CreativePreviewProduct, InventoryProductOut
from devfeed_http.partner_products import ManualProductInput, ManualProductUpdate
from pydantic import TypeAdapter, ValidationError

TAGLINE = TypeAdapter(Tagline)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AI IDE", "AI IDE"),
        ("  Build\tRust\nAPIs.  ", "Build Rust APIs."),
        ("**Build** [C++](https://cpp.example) and .NET APIs.", "Build C++ and .NET APIs."),
        ("<script>private</script><p>Ship APIs.</p>", "Ship APIs."),
        ("Use `git push` safely.", "Use git push safely."),
        ("C# and C++ support foo_bar and .NET.", "C# and C++ support foo_bar and .NET."),
        ("x" * 160, "x" * 160),
    ],
)
def test_taglines_are_plain_single_line_text(raw, expected):
    assert TAGLINE.validate_python(raw) == expected


@pytest.mark.parametrize(
    "value", [None, True, 42, {}, [], "", "  ", "AI", "x" * 161, "bad\0text", "bad\x01text"]
)
def test_api_taglines_reject_invalid_or_excessive_copy(value):
    with pytest.raises(ValidationError):
        TAGLINE.validate_python(value)


def test_import_conversion_uses_plain_first_sentence_and_a_bounded_word_boundary():
    value = "**Build** [C++](https://cpp.example) APIs. " + "Additional evidence. " * 50
    assert imported_tagline(value) == "Build C++ APIs."
    long = "Monitor production systems and inspect traces " * 20
    converted = imported_tagline(long)
    assert len(converted) <= 160 and converted.endswith("…")
    assert long.startswith(converted[:-1])
    assert imported_tagline("x" * 500) == "x" * 159 + "…"


def test_import_preserves_the_full_short_tagline_across_sentence_boundaries():
    tagline = "Cron fails silently. This doesn’t."
    assert imported_tagline(tagline) == tagline
    assert imported_tagline("**Cron fails silently.** This doesn’t.") == tagline


def test_connector_prefers_tagline_and_preserves_full_raw_source_and_independent_evidence():
    raw_description = (
        "**Long official capability description.** " + "Detailed source evidence. " * 50
    )
    source = {
        "id": "tool",
        "name": "Tool",
        "url": "https://tool.example",
        "listing_url": "https://catalog.example/tool",
        "tagline": "Cron fails silently. This doesn’t.",
        "description": raw_description,
    }
    config = ConnectorConfig(base_url="https://catalog.example")
    client = ConnectorClient(
        "catalog", config, fetch=lambda _: {"items": [source], "nextCursor": None}
    )
    candidates, _ = client.discover_page()
    item = client.product(candidates[0])
    assert item.tagline == source["tagline"]
    assert item.evidence == item.technologies == []
    assert candidates[0].data["description"] == raw_description
    assert source["description"] == raw_description

    source.pop("tagline")
    item = client.product(ProductCandidate(external_id="tool", data=source))
    assert item.tagline == "Long official capability description."
    assert source["description"] == raw_description


def test_configured_upstream_description_path_maps_only_to_tagline():
    config = ConnectorConfig(
        base_url="https://catalog.example", fields={"tagline": ["copy.description"]}
    )
    item = ConnectorClient("catalog", config).product(
        ProductCandidate(
            external_id="tool",
            data={
                "id": "tool",
                "name": "Tool",
                "url": "https://tool.example",
                "listing_url": "https://catalog.example/tool",
                "copy": {"description": "Monitor **C++** APIs. Full capability details follow."},
            },
        )
    )
    assert item.tagline == "Monitor C++ APIs. Full capability details follow."
    assert "description" not in item.model_dump()


def test_manual_products_and_import_inputs_require_tagline_without_an_old_field_alias():
    manual = {"name": "Tool", "product_url": "https://tool.example", "tagline": "AI IDE"}
    assert ManualProductInput.model_validate(manual).tagline == "AI IDE"
    assert ManualProductUpdate(tagline="AI IDE", expected_revision=1).tagline == "AI IDE"
    imported = {
        **manual,
        "provider": "catalog",
        "external_id": "tool",
        "listing_url": "https://catalog.example/tool",
    }
    assert ProductInput.model_validate(imported).tagline == "AI IDE"
    for model, value in [(ManualProductInput, manual), (ProductInput, imported)]:
        old = {**value, "description": value["tagline"]}
        old.pop("tagline")
        with pytest.raises(ValidationError):
            model.model_validate(old)
        with pytest.raises(ValidationError):
            model.model_validate({**value, "description": "Old display copy"})


def test_evaluation_snapshot_keeps_full_capability_quotes_separate_from_tagline():
    quote = "Python runtime metrics support application debugging. " * 10
    facts = ProductInput(
        provider="catalog",
        external_id="tool",
        name="Tool",
        product_url="https://tool.example",
        listing_url="https://catalog.example/tool",
        tagline="Inspect Python runtime metrics.",
        technologies=["Python"],
        evidence=[
            {
                "url": "https://tool.example/docs",
                "quote": quote,
                "capability": "Debug Python applications.",
            }
        ],
    )
    snapshot = product_snapshot(SimpleNamespace(**facts.model_dump(), revision=3))
    assert snapshot["tagline"] == facts.tagline and "description" not in snapshot
    assert snapshot["evidence"][0]["quote"] == quote
    assert snapshot["technologies"] == ["Python"]


@pytest.mark.parametrize(
    "model",
    [
        ProductOut,
        ListingOut,
        ProductSnapshot,
        CreativePreviewProduct,
        InventoryProductOut,
        PlacementOut,
    ],
)
def test_product_response_contracts_expose_only_bounded_taglines(model):
    schema = model.model_json_schema()
    assert "tagline" in schema["required"] and "description" not in schema["properties"]
    assert schema["properties"]["tagline"]["minLength"] == 3
    assert schema["properties"]["tagline"]["maxLength"] == 160


@pytest.mark.parametrize("model", [PartnerProduct, PartnerListing])
def test_product_storage_uses_a_tagline_column(model):
    assert "tagline" in model.__table__.c and "description" not in model.__table__.c
    assert model.__table__.c.tagline.type.length == 160
