"""Export the partner service contract without starting services or querying the database."""

import json
from pathlib import Path

from devfeed_core.partner_ad_formats import AUTOMATIC_AD_FORMATS
from devfeed_partner_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def build_contract() -> dict:
    document = create_app().openapi()
    # Account response validation retains the automatic-enrollment restriction,
    # which Pydantic's model validator cannot express in its JSON Schema.
    document["x-devfeed-automatic-ad-formats"] = list(AUTOMATIC_AD_FORMATS)
    return document


if __name__ == "__main__":
    target = ROOT / "apps/partner/openapi.json"
    target.write_text(json.dumps(build_contract(), indent=2, sort_keys=True) + "\n")
    print(f"Exported {target.relative_to(ROOT)}")
