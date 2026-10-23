"""Account and service boundaries through real authentication and durable writes."""

import json

import pytest
from devfeed_admin_api import auth as admin_auth
from devfeed_core.models import Article, ArticleBookmark, Tag
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "service,path",
    [
        ("public", "/v1/user/preferences"),
        ("public", "/v1/admin/users"),
        ("user", "/v1/admin/users"),
        ("admin", "/v1/user/preferences"),
    ],
)
def test_services_do_not_expose_other_services_routes(api_security, service, path):
    for headers in ({}, api_security.headers(), api_security.headers("admin")):
        assert (
            api_security.request("GET", path, service=service, headers=headers).status_code == 404
        )


@pytest.mark.parametrize(
    "service,path,other",
    [("user", "/v1/user/preferences", "admin"), ("admin", "/v1/admin/users", "user")],
)
def test_private_apis_require_their_own_session(api_security, service, path, other):
    for headers in (
        {},
        api_security.headers(other),
        {"Cookie": "__Host-devfeed_user_session=invalid"},
    ):
        assert (
            api_security.request("GET", path, service=service, headers=headers).status_code == 401
        )
    assert api_security.request("GET", path, service=service).status_code == 200


def test_a_user_record_cannot_be_replayed_as_an_admin(api_security):
    reader = api_security.readers[0]
    record = {
        **reader["identity"],
        "expires_at": 4102444800,
        "policy": admin_auth.oidc.policy_key(admin_auth.get_settings()),
    }
    api_security.store.set(admin_auth.key("session", reader["token"]), json.dumps(record), ex=3600)
    headers = {"Cookie": f"__Host-devfeed_admin_session={reader['token']}"}
    assert (
        api_security.request("GET", "/v1/admin/users", service="admin", headers=headers).status_code
        == 401
    )


def test_admin_requires_the_admin_role(api_security):
    token = api_security.admin["token"]
    record = json.loads(api_security.store.get(admin_auth.key("session", token)))
    record["roles"] = []
    api_security.store.set(admin_auth.key("session", token), json.dumps(record), ex=3600)
    assert api_security.request("GET", "/v1/admin/users", service="admin").status_code == 403


@pytest.mark.parametrize(
    "change",
    [{"X-CSRF-Token": ""}, {"X-CSRF-Token": "A" * 43}, {"Origin": "https://foreign.fuzz.invalid"}],
)
def test_writes_require_the_matching_csrf_and_origin(api_security, change):
    headers = {**api_security.headers(), **change}
    response = api_security.request(
        "PUT",
        f"/v1/user/articles/{api_security.article_id}/bookmark",
        headers=headers,
        json={"bookmarked": True},
    )
    assert response.status_code == 403
    with api_security.database() as session:
        assert session.scalars(select(ArticleBookmark)).all() == []


@pytest.mark.parametrize(
    "change",
    [{"X-CSRF-Token": ""}, {"X-CSRF-Token": "A" * 43}, {"Origin": "https://foreign.fuzz.invalid"}],
)
def test_admin_writes_require_the_matching_csrf_and_origin(api_security, change):
    response = api_security.request(
        "POST",
        "/v1/admin/tags",
        service="admin",
        headers={**api_security.headers("admin"), **change},
        json={"name": "Forged tag", "slug": "forged-tag"},
    )
    assert response.status_code == 403
    with api_security.database() as session:
        assert session.scalar(select(Tag).where(Tag.slug == "forged-tag")) is None


def test_unpublished_articles_are_only_available_to_admins(api_security):
    with api_security.database.begin() as session:
        session.get(Article, api_security.article_id).publication_status = "unpublished"
    article = api_security.article_id
    assert (
        api_security.request("GET", f"/v1/articles/{article}", service="public").status_code == 404
    )
    assert (
        api_security.request(
            "PUT", f"/v1/user/articles/{article}/bookmark", json={"bookmarked": True}
        ).status_code
        == 404
    )
    assert (
        api_security.request("GET", f"/v1/admin/articles/{article}", service="admin").status_code
        == 200
    )
    with api_security.database() as session:
        assert session.scalars(select(ArticleBookmark)).all() == []


def test_bookmark_removal_and_preferences_are_owned_by_the_session(api_security):
    article = f"/v1/user/articles/{api_security.article_id}/bookmark"
    assert api_security.request("PUT", article, json={"bookmarked": True}).status_code == 200
    assert (
        api_security.request("PUT", article, reader=1, json={"bookmarked": False}).status_code
        == 200
    )
    assert api_security.request("GET", "/v1/user/bookmarks", reader=1).json()["items"] == []
    assert [
        row["id"] for row in api_security.request("GET", "/v1/user/bookmarks").json()["items"]
    ] == [str(api_security.article_id)]
    body = {"topic_ids": [str(api_security.topic_id)]}
    assert api_security.request("PUT", "/v1/user/preferences", json=body).status_code == 200
    assert (
        api_security.request(
            "PUT", "/v1/user/preferences", reader=1, json={"topic_ids": []}
        ).status_code
        == 200
    )
    assert api_security.request("GET", "/v1/user/preferences").json() == body
    assert api_security.request("GET", "/v1/user/preferences", reader=1).json() == {"topic_ids": []}
    attempted = {"bookmarked": False, "user_id": api_security.readers[0]["identity"]["user_id"]}
    assert api_security.request("PUT", article, reader=1, json=attempted).status_code == 422


@pytest.mark.parametrize("payload", [b'{"bookmarked":', b"[]", b'{"bookmarked":"invalid"}', b"{}"])
def test_malformed_bookmark_input_returns_validation_error(api_security, payload):
    response = api_security.request(
        "PUT",
        f"/v1/user/articles/{api_security.article_id}/bookmark",
        content=payload,
        headers={**api_security.headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize(
    "value,expected", [(0, False), (1, True), ("false", False), ("true", True)]
)
def test_supported_boolean_coercion_still_returns_a_typed_response(api_security, value, expected):
    response = api_security.request(
        "PUT",
        f"/v1/user/articles/{api_security.article_id}/bookmark",
        json={"bookmarked": value},
    )
    assert response.status_code == 200
    assert response.json()["bookmarked"] is expected


@pytest.mark.parametrize("path", ["/v1/tags", "/v1/search/suggestions"])
def test_extra_query_parameters_are_ignored(api_security, path):
    expected = api_security.request("GET", path, service="public")
    response = api_security.request(
        "GET", path, service="public", params={"extra": "schema-unknown-query"}
    )
    assert expected.status_code == response.status_code == 200
    assert expected.json() == response.json()


@pytest.mark.parametrize(
    "payload",
    [{field: "before\x00after"} for field in ("display_name", "bio", "location", "about")]
    + [{"links": [{"url": "https://profile.fuzz.invalid", "label": "before\x00after"}]}],
)
def test_profile_nul_text_is_rejected_without_changing_the_account(api_security, payload):
    path = "/v1/user/settings/profile"
    original = api_security.request("GET", path).json()
    response = api_security.request("PUT", path, json=payload)
    assert response.status_code == 422
    assert api_security.request("GET", path).json() == original
