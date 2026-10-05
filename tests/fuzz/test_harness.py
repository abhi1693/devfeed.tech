"""Verify that the configured classifier actually rejects server and schema failures."""

import pytest
import schemathesis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from schemathesis.core.failures import FailureGroup
from test_api_fuzz import CHECKS, SELECTED_PATHS, in_fuzz_scope

pytestmark = pytest.mark.integration


def test_scope_limits_the_actual_generated_operations():
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Scope regression", "version": "1"},
        "paths": {
            path: {
                method: {"responses": {"200": {"description": "OK"}}} for method in ("get", "post")
            }
            for path in SELECTED_PATHS + ["/v1/user/auth/login", "/v1/admin/sources/import"]
        },
    }
    schema = schemathesis.openapi.from_dict(document).include(in_fuzz_scope)
    operations = [result.ok() for result in schema.get_all_operations()]
    assert operations
    assert {operation.path for operation in operations} == set(SELECTED_PATHS)
    assert all(
        operation.method.upper() == "GET"
        for operation in operations
        if operation.path.startswith("/v1/admin/")
    )


@pytest.mark.parametrize("failure", ["server", "schema"])
def test_configured_checks_reject_broken_responses(failure):
    class Item(BaseModel):
        id: int

    app = FastAPI()

    @app.get("/item", response_model=Item)
    def item():
        return JSONResponse({"id": "wrong type"}, status_code=500 if failure == "server" else 200)

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    case = schema["/item"]["GET"].Case()
    with pytest.raises(FailureGroup):
        case.call_and_validate(checks=CHECKS)
