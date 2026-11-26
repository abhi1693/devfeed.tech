"""Bounded schema fuzzing and stateful ownership checks on the real APIs."""

import json
import os
from pathlib import Path

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test
from schemathesis.checks import (
    content_type_conformance,
    not_a_server_error,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
    unsupported_method,
)
from schemathesis.generation import GenerationMode

pytestmark = pytest.mark.integration
PROFILE = os.environ["DEVFEED_FUZZ_PROFILE"]
NIGHTLY = PROFILE == "nightly"
REPORTS = Path(os.environ["DEVFEED_FUZZ_REPORTS"])
# FastAPI supports boolean coercion and extra query parameters. Generate both
# valid and invalid inputs, then check responses here; explicit boundary tests
# assert 422 for malformed JSON, missing fields and unusable database parameters.
CHECKS = (
    not_a_server_error,
    status_code_conformance,
    content_type_conformance,
    response_schema_conformance,
    response_headers_conformance,
    unsupported_method,
)
PATHS = {
    "public": {
        "/v1/feed",
        "/v1/feed/options",
        "/v1/articles/{article_id}",
        "/v1/topics",
        "/v1/tags",
        "/v1/sources",
    },
    "user": {
        "/v1/user/preferences",
        "/v1/user/preferences/topics/{topic_id}",
        "/v1/user/bookmarks",
        "/v1/user/articles/{article_id}/bookmark",
        "/v1/user/articles/{article_id}/like",
        "/v1/user/settings/profile",
    },
    "admin": {
        "/v1/admin/users",
        "/v1/admin/users/{user_id}",
        "/v1/admin/articles",
        "/v1/admin/articles/{article_id}",
    },
}
NIGHTLY_PATHS = {
    "public": {
        "/v1/topics/{slug}",
        "/v1/topics/{slug}/relations",
        "/v1/tags/{slug}",
        "/v1/sources/{source_id}",
        "/v1/search/suggestions",
    },
    "user": {
        "/v1/user/preferences/sources",
        "/v1/user/preferences/sources/{source_id}",
        "/v1/user/settings/appearance",
        "/v1/user/settings/feed",
        "/v1/user/settings/reading-heatmap",
        "/v1/user/engagement",
    },
    "admin": {
        "/v1/admin/tags",
        "/v1/admin/tags/{tag_id}",
        "/v1/admin/topics",
        "/v1/admin/topics/{topic_id}",
        "/v1/admin/sources",
        "/v1/admin/sources/{source_id}",
    },
}
SELECTED_PATHS = sorted(
    {
        path
        for service in PATHS
        for path in PATHS[service] | (NIGHTLY_PATHS[service] if NIGHTLY else set())
    }
)


def in_fuzz_scope(context):
    operation = context.operation
    return operation.path in SELECTED_PATHS and (
        not operation.path.startswith("/v1/admin/") or operation.method.upper() == "GET"
    )


@pytest.fixture(params=("public", "user", "admin"))
def fuzz_schema(request, api_security):
    service = request.param
    config = schemathesis.Config.from_dict(
        {
            "generation": {
                "max-examples": 100 if NIGHTLY else 12,
                "with-security-parameters": False,
                "mode": "all",
            },
            "phases": {"coverage": {"enabled": NIGHTLY, "unexpected-methods": ["TRACE"]}},
        }
    )
    schema = schemathesis.openapi.from_asgi(
        "/openapi.json", api_security.apps[service], config=config
    )
    paths = PATHS[service] | (NIGHTLY_PATHS[service] if NIGHTLY else set())
    missing = paths - schema.raw_schema["paths"].keys()
    assert not missing, f"Fuzz targets disappeared: {missing}"
    schema = schema.include(in_fuzz_scope)
    operations = [result.ok() for result in schema.get_all_operations()]
    assert {operation.path for operation in operations} == paths
    (REPORTS / f"{service}.openapi.json").write_text(json.dumps(schema.raw_schema, indent=2))
    (REPORTS / f"{service}.targets.json").write_text(
        json.dumps(
            [
                {"method": operation.method.upper(), "path": operation.path}
                for operation in operations
            ],
            indent=2,
        )
    )
    return schema


@schemathesis.pytest.from_fixture("fuzz_schema").include(in_fuzz_scope).parametrize()
@settings(
    max_examples=100 if NIGHTLY else 12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_api_contract(case, api_security, fuzz_schema):
    service = next(name for name, app in api_security.apps.items() if app is fuzz_schema.app)
    assert case.path in PATHS[service] | (NIGHTLY_PATHS[service] if NIGHTLY else set())
    # Coverage also probes TRACE, which cannot invoke an admin mutation.
    assert service != "admin" or case.method in {"GET", "TRACE"}
    if case.meta.generation.mode == GenerationMode.POSITIVE and case.path_parameters:
        for name, value in {
            "article_id": api_security.article_id,
            "topic_id": api_security.topic_id,
            "source_id": api_security.source_id,
            "tag_id": api_security.tag_id,
            "user_id": api_security.readers[0]["identity"]["user_id"],
        }.items():
            if name in case.path_parameters:
                case.path_parameters[name] = str(value)
        if "slug" in case.path_parameters:
            case.path_parameters["slug"] = (
                "fuzz-tag" if case.path.startswith("/v1/tags/") else "fuzz-engineering"
            )
    headers = {} if service == "public" else api_security.headers(service)
    case.call_and_validate(headers=headers, checks=CHECKS)


def test_reader_sequences_preserve_account_ownership(api_security):
    schema = schemathesis.openapi.from_asgi("/openapi.json", api_security.apps["user"])
    article = str(api_security.article_id)
    topic = str(api_security.topic_id)

    class ReaderSequences(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.saved = [False, False]
            self.liked = [False, False]
            self.followed = [False, False]
            for reader in range(2):
                self.call(
                    reader,
                    "/v1/user/articles/{article_id}/bookmark",
                    body={"bookmarked": False},
                    path_parameters={"article_id": article},
                )
                self.call(
                    reader,
                    "/v1/user/articles/{article_id}/like",
                    body={"liked": False},
                    path_parameters={"article_id": article},
                )
                self.call(reader, "/v1/user/preferences", body={"topic_ids": []})

        def call(self, reader, path, method="PUT", **kwargs):
            case = schema[path][method].Case(**kwargs)
            response = case.call(headers=api_security.headers(reader=reader))
            case.validate_response(response, checks=CHECKS)
            assert response.status_code == 200
            return response.json()

        @rule(reader=st.integers(0, 1), value=st.booleans())
        def bookmark(self, reader, value):
            self.call(
                reader,
                "/v1/user/articles/{article_id}/bookmark",
                body={"bookmarked": value},
                path_parameters={"article_id": article},
            )
            self.saved[reader] = value

        @rule(reader=st.integers(0, 1), value=st.booleans())
        def like(self, reader, value):
            self.call(
                reader,
                "/v1/user/articles/{article_id}/like",
                body={"liked": value},
                path_parameters={"article_id": article},
            )
            self.liked[reader] = value

        @rule(reader=st.integers(0, 1), value=st.booleans())
        def follow(self, reader, value):
            self.call(
                reader,
                "/v1/user/preferences/topics/{topic_id}",
                body={"followed": value},
                path_parameters={"topic_id": topic},
            )
            self.followed[reader] = value

        @invariant()
        def private_views_match_each_reader(self):
            for reader in range(2):
                saved = self.call(reader, "/v1/user/bookmarks", "GET")
                assert [row["id"] for row in saved["items"]] == (
                    [article] if self.saved[reader] else []
                )
                preferences = self.call(reader, "/v1/user/preferences", "GET")
                assert preferences["topic_ids"] == ([topic] if self.followed[reader] else [])
                engagement = self.call(
                    reader, "/v1/user/engagement", "GET", query={"article_id": [article]}
                )[0]
                assert engagement["liked"] is self.liked[reader]
                assert engagement["bookmarked"] is self.saved[reader]
                assert engagement["likes"] == sum(self.liked)

    run_state_machine_as_test(
        ReaderSequences,
        settings=settings(
            max_examples=40 if NIGHTLY else 8,
            stateful_step_count=30 if NIGHTLY else 6,
            deadline=None,
        ),
    )
