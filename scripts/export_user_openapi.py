"""Export reader operations without starting services or querying the database."""

import json
from pathlib import Path

from devfeed_api.main import create_app as create_public_app
from devfeed_http.operation_policies import ReaderGatewayPolicy, export_gateway_document
from devfeed_user_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def build_contract() -> dict:
    document = export_gateway_document(create_app(), policy_type=ReaderGatewayPolicy)
    document["x-devfeed-public-operations"] = sorted(
        (
            {"operationId": operation["operationId"], "method": method.upper(), "path": path}
            for path, methods in create_public_app().openapi()["paths"].items()
            for method, operation in methods.items()
            if method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        ),
        key=lambda operation: (operation["path"], operation["method"], operation["operationId"]),
    )
    return document


if __name__ == "__main__":
    target = ROOT / "apps/web/openapi.json"
    target.write_text(json.dumps(build_contract(), indent=2, sort_keys=True) + "\n")
    print(f"Exported {target.relative_to(ROOT)}")
