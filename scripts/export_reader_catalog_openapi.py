"""Export only APIs actually exposed by the reader's same-origin gateway."""

import copy
import json
from pathlib import Path

from devfeed_api.main import create_app as create_public_app

from scripts.export_user_openapi import build_contract

ROOT = Path(__file__).resolve().parents[1]


def build_catalog_contract() -> dict:
    user = build_contract()
    public = create_public_app().openapi()
    bindings = json.loads((ROOT / "apps/web/src/lib/api/reader-bindings.json").read_text())
    paths = {}
    for route in user["x-devfeed-gateway-routes"]:
        method = route["method"].lower()
        operation = user["paths"].get(route["path"], {}).get(method)
        if operation:
            paths.setdefault("/api" + route["path"], {})[method] = copy.deepcopy(operation)
    public_operations = {
        operation["operationId"]: operation
        for methods in public["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    for binding in bindings["bindings"]:
        # Composite gateways have reader-specific semantics: describe only the
        # anonymous GET API here, not tracking or placement ingestion contracts.
        if (
            binding["method"] != "GET"
            or not binding["backendOperations"]
            or binding["backendOperations"][0] not in public_operations
        ):
            continue
        operation = copy.deepcopy(public_operations[binding["backendOperations"][0]])
        operation["operationId"] = binding["operationId"]
        path = "/api/" + binding["routeFile"].removesuffix("/route.ts")
        parameters = [p for p in operation.get("parameters", []) if p["in"] != "path"]
        for parameter in binding["parameters"]:
            path = path.replace("[" + parameter["name"] + "]", "{" + parameter["name"] + "}")
            parameters.append(
                {
                    "name": parameter["name"],
                    "in": "path",
                    "required": True,
                    "schema": parameter["schema"],
                }
            )
        response = operation["responses"]["200"]["content"]["application/json"]
        if binding["operationId"] in {"readerTopics", "readerSources", "readerTags"}:
            response["schema"] = {
                "type": "object",
                "required": ["items", "next_cursor"],
                "properties": {
                    "items": response["schema"],
                    "next_cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            }
            allowed = (
                {"offset", "q", "sort"}
                if binding["operationId"] == "readerTopics"
                else {"offset", "q", "letter"}
            )
            parameters = [p for p in parameters if p["name"] in allowed]
        elif binding["operationId"] == "readerArticle":
            response["schema"] = {
                "type": "object",
                "required": ["article", "topic"],
                "properties": {
                    "article": response["schema"],
                    "topic": {
                        "anyOf": [{"$ref": "#/components/schemas/TopicOut"}, {"type": "null"}]
                    },
                },
            }
        operation["parameters"] = parameters
        paths.setdefault(path, {})["get"] = operation
    # Internal runtime/gateway policies are build inputs, not public API metadata.
    for methods in paths.values():
        for operation in methods.values():
            for key in list(operation):
                if key.startswith("x-devfeed-"):
                    del operation[key]
    schemas = {**public["components"]["schemas"], **user["components"]["schemas"]}
    # Retain only reachable schemas, avoiding unrelated service metadata.
    used = set()
    pending = [paths]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in used:
                    used.add(name)
                    pending.append(schemas[name])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return {
        "openapi": user["openapi"],
        "info": {
            "title": "DevFeed Reader API",
            "version": user["info"]["version"],
            "description": (
                "Public discovery reads and optional account operations "
                "through the reader gateway. "
                "Account operations require an OIDC session; writes also require CSRF protection. "
                "See /api/docs."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": {name: schemas[name] for name in sorted(used)},
            "securitySchemes": user["components"]["securitySchemes"],
        },
    }


if __name__ == "__main__":
    target = ROOT / "apps/web/reader-openapi.json"
    target.write_text(json.dumps(build_catalog_contract(), indent=2, sort_keys=True) + "\n")
    print(f"Exported {target.relative_to(ROOT)}")
