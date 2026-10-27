"""Admin preference ownership and moderation adapters preserve authenticated authority.

Domain writes and scoped query rows are boundaries. Units verify HTTP validation,
response privacy and forwarding, without claiming SQL selection or atomic writes.
"""

import hashlib
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import ingestion, search_suggestions, user_settings
from devfeed_admin_api.config import Settings
from devfeed_core.models import ArticleEnrichmentJob, IngestionJob, SearchQueryStat
from devfeed_core.search_suggestions import query_hash
from devfeed_core.user_settings import UserSettings
from pydantic import ValidationError

from tests.support.admin_editorial import operator_api
from tests.support.source_readiness import NOW, job


@pytest.mark.parametrize("changed", ["none", "issuer", "subject", "organization_id"])
def test_settings_read_uses_exact_authenticated_identity_scope_and_closes_the_read_session(
    monkeypatch, changed
):
    current = UserSettings(profile={"display_name": "Editorial operator"})
    read = Mock(return_value=current)
    monkeypatch.setattr(user_settings, "read_settings", read)
    with operator_api(user_settings) as api:
        if changed != "none":
            setattr(api.admin, changed, "different-identity-component")
        response = api.client.get("/v1/admin/settings")
        assert (
            response.status_code == 200
            and response.json()["profile"]["display_name"] == "Editorial operator"
        )
        scope = hashlib.sha256(
            json.dumps(
                [api.admin.issuer, api.admin.organization_id, api.admin.subject],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        read.assert_called_once_with(api.session, scope)
        api.session.close.assert_called_once_with()
        api.session.commit.assert_not_called()


def test_failed_preference_read_releases_session_before_propagating_failure(monkeypatch):
    monkeypatch.setattr(
        user_settings, "read_settings", Mock(side_effect=RuntimeError("read unavailable"))
    )
    with operator_api(user_settings) as api:
        with pytest.raises(RuntimeError, match="read unavailable"):
            api.client.get("/v1/admin/settings")
        api.session.close.assert_called_once_with()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "section,body",
    [
        ("profile", {"display_name": "  Operator  ", "avatar_url": ""}),
        ("notifications", {"show_badge": False, "sound": True}),
        (
            "appearance",
            {
                "theme": "dark",
                "timezone": "Asia/Kolkata",
                "density": "compact",
                "reduce_motion": True,
            },
        ),
        (
            "defaults",
            {
                "refresh_seconds": 0,
                "page_size": 50,
                "remember_filters": False,
                "landing_page": "/jobs/analysis",
            },
        ),
    ],
)
def test_section_write_preserves_authenticated_owner_and_returns_saved_settings(
    monkeypatch, section, body
):
    saved = []

    def write(session, owner, chosen_section, document):
        saved.append((session, owner, chosen_section, document))
        return UserSettings(**{chosen_section: document})

    monkeypatch.setattr(user_settings, "write_settings", write)
    with operator_api(user_settings) as api:
        response = api.client.put(f"/v1/admin/settings/{section}", json=body)
        assert response.status_code == 200, response.text
        session, owner, chosen_section, document = saved[0]
        assert session is api.session and owner == user_settings.account(api.admin)
        assert chosen_section == section and response.json()[section] == document
        if section == "profile":
            assert document == {"display_name": "Operator", "avatar_url": None}
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "section,body",
    [
        ("profile", {"owner": "another-admin"}),
        ("profile", {"avatar_url": "http://127.0.0.1/private"}),
        ("appearance", {"timezone": "unknown/timezone"}),
        ("defaults", {"refresh_seconds": 1}),
        ("notifications", {"secret_option": True}),
    ],
)
def test_invalid_or_owner_spoofing_preference_input_never_reaches_write_boundary(
    monkeypatch, section, body
):
    write = Mock()
    monkeypatch.setattr(user_settings, "write_settings", write)
    with operator_api(user_settings) as api:
        assert api.client.put(f"/v1/admin/settings/{section}", json=body).status_code == 422
        write.assert_not_called()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("body", [{"columns": {"title": False}}, {"query": {"q": "Angular"}}])
def test_table_patch_keeps_omitted_sections_out_of_write_and_reset_is_explicit(monkeypatch, body):
    write = Mock(
        side_effect=lambda _session, _owner, _section, document, **kw: UserSettings(
            tables={kw["table"]: document} if kw.get("table") else {}
        )
    )
    monkeypatch.setattr(user_settings, "write_settings", write)
    with operator_api(user_settings) as api:
        assert api.client.patch("/v1/admin/settings/tables/articles", json=body).status_code == 200
        write.assert_called_once_with(
            api.session, user_settings.account(api.admin), "tables", body, table="articles"
        )
        api.session.commit.assert_called_once_with()
        api.session.commit.reset_mock()
        write.reset_mock()
        assert api.client.delete("/v1/admin/settings/tables").status_code == 200
        write.assert_called_once_with(api.session, user_settings.account(api.admin), "tables", {})
        api.session.commit.assert_called_once_with()


def test_invalid_table_key_or_cardinality_is_a_safe_validation_error_without_commit(monkeypatch):
    monkeypatch.setattr(
        user_settings, "write_settings", Mock(side_effect=ValueError("private stored settings"))
    )
    with operator_api(user_settings) as api:
        response = api.client.patch(
            "/v1/admin/settings/tables/unknown!", json={"query": {"q": "Angular"}}
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid table preference key or too many saved tables"
        assert "private" not in response.text
        api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        {"oidc_google_idp_id": "provider with spaces"},
        {"admin_base_url": "https://operator:private@admin.example"},
        {"admin_base_url": "https://admin.example/content"},
        {"admin_base_url": "http://localhost:3001"},
        {"admin_base_url": "https://admin.example", "admin_cookie_secure": False},
        {"oidc_organization_id": "two organizations"},
        {"oidc_organization_id": "org", "oidc_organization_scope_template": "org:{missing}"},
        {"oidc_organization_id": "org", "oidc_organization_scope_template": "fixed-scope"},
        {
            "oidc_organization_id": "org",
            "oidc_organization_scope_template": "org {organization_id}",
        },
        {"oidc_organization_id": "org", "oidc_organization_claim": " "},
        {
            "notifications_enabled": True,
            "chimely_api_url": "https://notifications.example",
            "chimely_admin_environment": "admin",
            "chimely_admin_hmac_secret": "replace-me",
        },
    ],
)
def test_admin_configuration_fails_closed_for_unsafe_origin_identity_or_inbox_secret(values):
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, **values)
    assert "operator:private" not in str(failure.value)


def test_local_admin_development_and_explicit_provider_scope_are_valid_configurations():
    settings = Settings(
        _env_file=None,
        admin_base_url="http://localhost:3001",
        admin_cookie_secure=False,
        oidc_issuer_url="http://127.0.0.1:8080",
        oidc_google_idp_id="google-1",
        oidc_organization_id="org",
        oidc_organization_scope_template="organization:{organization_id}",
        oidc_organization_claim="organization",
    )
    assert settings.admin_cookie_secure is False and settings.oidc_google_idp_id == "google-1"


def suggestion(**changes):
    return SearchQueryStat(
        **{
            "query_hash": query_hash("angular router"),
            "query": "angular router",
            "status": "pending",
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": None,
            **changes,
        }
    )


class SearchAnalyticsFake:
    def __init__(self, _client):
        pass

    async def rank(self, _session, **_kwargs):
        value = suggestion()
        return [
            {
                "query_hash": value.query_hash,
                "query": value.query,
                "status": value.status,
                "reviewed_at": value.reviewed_at,
                "review_note": value.review_note,
                "successful_count": 14,
                "first_seen_at": NOW - timedelta(days=3),
                "last_seen_at": NOW - timedelta(minutes=1),
            }
        ]

    async def get(self, _digest):
        return {
            "successful_count": 14,
            "first_seen_at": NOW - timedelta(days=3),
            "last_seen_at": NOW - timedelta(minutes=1),
        }


def test_search_moderation_list_exposes_aggregate_facts_without_reader_identity(monkeypatch):
    current = suggestion()
    monkeypatch.setattr(search_suggestions, "SearchSuggestionAnalytics", SearchAnalyticsFake)
    with operator_api(search_suggestions) as api:
        api.app.state.analytics = SimpleNamespace(client=object())
        response = api.client.get(
            "/v1/admin/search-suggestions", params={"status": "pending", "minimum": 5, "limit": 1}
        )
        assert response.status_code == 200
        (item,) = response.json()
        assert item["query_hash"] == current.query_hash and item["successful_count"] == 14
        assert item["status"] == "pending" and item["reviewed_at"] is None
        assert set(item) == set(search_suggestions.SearchSuggestionOut.model_fields)
        assert not ({"user_id", "account_id", "session", "ip_address"} & item.keys())


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_suggestion_review_uses_authenticated_reviewer_and_saved_response(monkeypatch, status):
    current = suggestion()

    def review(session, digest, **values):
        assert digest == current.query_hash
        current.status, current.reviewed_at = values["status"], NOW
        current.reviewed_by, current.review_note = values["reviewer"], values["note"]
        return current

    apply = Mock(side_effect=review)
    monkeypatch.setattr(search_suggestions, "review_query", apply)
    monkeypatch.setattr(search_suggestions, "SearchSuggestionAnalytics", SearchAnalyticsFake)
    with operator_api(search_suggestions) as api:
        api.app.state.analytics = SimpleNamespace(client=object())
        api.session.get.return_value = current
        response = api.client.post(
            f"/v1/admin/search-suggestions/{current.query_hash}/review",
            json={"status": status, "note": "Checked aggregate query"},
        )
        assert response.status_code == 200 and response.json()["status"] == status
        apply.assert_called_once_with(
            api.session,
            current.query_hash,
            status=status,
            reviewer=api.admin.subject,
            note="Checked aggregate query",
        )
        assert current.reviewed_by == api.admin.subject
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "error,code,detail",
    [
        (ValueError("private details"), 422, "Invalid search suggestion hash"),
        (LookupError("private details"), 404, "Search suggestion not found"),
    ],
)
def test_suggestion_review_boundary_errors_are_safe_and_do_not_commit(
    monkeypatch, error, code, detail
):
    monkeypatch.setattr(search_suggestions, "review_query", Mock(side_effect=error))
    monkeypatch.setattr(search_suggestions, "SearchSuggestionAnalytics", SearchAnalyticsFake)
    with operator_api(search_suggestions) as api:
        api.app.state.analytics = SimpleNamespace(client=object())
        api.session.get.return_value = suggestion()
        response = api.client.post(
            f"/v1/admin/search-suggestions/{'a' * 64}/review", json={"status": "rejected"}
        )
        assert response.status_code == code and response.json()["detail"] == detail
        assert "private" not in response.text
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("lane", ["article-jobs", "jobs"])
@pytest.mark.parametrize("present", [False, True])
def test_ingestion_compatibility_detail_has_typed_missing_error_and_no_lease_secrets(lane, present):
    identifier = uuid.uuid4()
    current = (
        job(
            ArticleEnrichmentJob,
            article_id=uuid.uuid4(),
            result={"input_url": "https://publisher.example/article"},
            changed_fields=[],
            http_status=None,
            dispatched_at=None,
            outcome=None,
        )
        if lane == "article-jobs"
        else job(
            IngestionJob,
            source_id=uuid.uuid4(),
            entries_seen=0,
            articles_created=0,
            entries_skipped=0,
            http_status=None,
            dispatched_at=None,
            automatic=False,
            new_source_entries=0,
        )
    )
    current.id = identifier
    with operator_api(ingestion) as api:
        api.session.get.return_value = current if present else None
        response = api.client.get(f"/v1/admin/ingestion/{lane}/{identifier}")
        assert response.status_code == (200 if present else 404)
        if present:
            assert response.json()["id"] == str(current.id)
            assert "lease_token" not in response.json() and "lease_until" not in response.json()
        else:
            assert "not found" in response.json()["detail"]


@pytest.mark.parametrize("lane", ["article-jobs", "jobs"])
def test_empty_ingestion_compatibility_page_is_a_valid_bounded_read_without_fabricated_work(lane):
    with operator_api(ingestion) as api:
        api.session.scalars.return_value.all.return_value = []
        response = api.client.get(
            f"/v1/admin/ingestion/{lane}",
            params={
                "article_id" if lane == "article-jobs" else "source_id": str(uuid.uuid4()),
                "status": "queued",
                "limit": 1,
                "offset": 5,
            },
        )
        assert response.status_code == 200 and response.json() == []
        api.session.add.assert_not_called()
        api.session.commit.assert_not_called()
