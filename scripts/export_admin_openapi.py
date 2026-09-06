"""Export the admin contract without starting services or querying the database."""

import json
from pathlib import Path

from devfeed_admin_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    document = create_app().openapi()
    target = ROOT / "apps/admin/openapi.json"
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"Exported {target.relative_to(ROOT)}")
