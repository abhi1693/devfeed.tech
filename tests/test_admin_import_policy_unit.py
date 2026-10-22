"""Publisher imports remain bounded discovery requests with explicit manual decisions."""

import hashlib
import uuid
from unittest.mock import Mock

import pytest
from devfeed_admin_api import source_imports as imports
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from test_admin_editorial_policy_unit import operator_api
from test_source_readiness_unit import NOW, configuration


@pytest.fixture
def api(monkeypatch):
    command = Mock(return_value={"created": 1, "candidates": [uuid.uuid4(), uuid.uuid4()]})
    monkeypatch.setattr(imports.discovery, "import_publishers", command)
    with operator_api(imports) as value:
        value.command = command
        yield value


@pytest.mark.parametrize("automatic", [False, True])
def test_pasted_import_deduplicates_hints_and_queues_discovery_without_trusting_or_approving_feeds(
    api, monkeypatch, automatic
):
    monkeypatch.setattr(
        imports,
        "get_settings",
        lambda: configuration(ai_enabled=automatic, full_automation=automatic),
    )
    content = "https://publisher.example/\nhttps://publisher.example/#duplicate\nhttps://second.example/engineering\n"
    response = api.client.post(
        "/v1/admin/source-imports",
        json={"format": "urls", "content": content, "name": "Engineering list"},
    )
    assert response.status_code == 201
    assert response.json() == {"created": 1, "existing": 1, "total": 2}
    hints, provenance = api.command.call_args.args
    assert [(hint.homepage, hint.feed_hint) for hint in hints] == [
        ("https://publisher.example/", None),
        ("https://second.example/engineering", None),
    ]
    assert provenance == f"admin:{api.admin.subject}:Engineering list"
    assert api.command.call_args.kwargs == {
        "checksum": hashlib.sha256(content.encode()).hexdigest(),
        "background": True,
        "assess_after": automatic,
    }
    api.session.commit.assert_not_called()


def test_remote_opml_keeps_feed_urls_as_untrusted_hints_and_attributes_the_collection(
    api, monkeypatch
):
    content = (
        b'<opml version="2.0"><body><outline title="Engineering" '
        b'htmlUrl="https://publisher.example/" '
        b'xmlUrl="https://publisher.example/feed"/></body></opml>'
    )
    api.command.return_value = {"created": 1, "candidates": [uuid.uuid4()]}
    crawler = Mock()
    crawler.get.return_value = FetchResult(200, content, "https://lists.example/publishers.opml")
    monkeypatch.setattr(imports, "CrawlSession", Mock(return_value=crawler))
    response = api.client.post(
        "/v1/admin/source-imports",
        json={"format": "opml", "url": "https://lists.example/publishers.opml"},
    )
    assert response.status_code == 201 and response.json()["total"] == 1
    crawler.get.assert_called_once_with("https://lists.example/publishers.opml")
    hints, origin = api.command.call_args.args
    assert hints[0].name == "Engineering" and hints[0].feed_hint == "https://publisher.example/feed"
    assert origin == f"admin:{api.admin.subject}:https://lists.example/publishers.opml"
    assert api.command.call_args.kwargs["checksum"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "body",
    [
        {"format": "urls"},
        {
            "format": "urls",
            "url": "https://lists.example/",
            "content": "https://publisher.example/",
        },
        {"format": "urls", "url": "http://127.0.0.1/private-token"},
    ],
)
def test_import_requires_exactly_one_public_input_before_fetch_or_discovery(api, monkeypatch, body):
    crawler = Mock()
    monkeypatch.setattr(imports, "CrawlSession", crawler)
    response = api.client.post("/v1/admin/source-imports", json=body)
    assert response.status_code == 422 and "private-token" not in response.text
    crawler.assert_not_called()
    api.command.assert_not_called()


@pytest.mark.parametrize(
    "content",
    [
        "https://publisher.example/\x00private-input",
        "http://localhost/private-input",
        "not-a-public-url",
    ],
)
def test_invalid_collection_does_not_echo_untrusted_content_or_start_discovery(api, content):
    response = api.client.post(
        "/v1/admin/source-imports", json={"format": "urls", "content": content}
    )
    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Invalid collection. Check the format, public URLs, and 1 MB / 1,000-entry limits."
    )
    assert "private-input" not in response.text
    api.command.assert_not_called()


def test_empty_collection_is_an_actionable_error_instead_of_a_successful_zero_import(api):
    response = api.client.post(
        "/v1/admin/source-imports",
        json={"format": "urls", "content": "# A list with no publishers"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "No publisher entries found in the collection."
    api.command.assert_not_called()


def test_failed_collection_fetch_exposes_only_its_safe_category(api, monkeypatch):
    crawler = Mock()
    crawler.get.side_effect = FeedError(
        "provider url?token=private-secret", reason="http_error", status=503
    )
    monkeypatch.setattr(imports, "CrawlSession", Mock(return_value=crawler))
    response = api.client.post(
        "/v1/admin/source-imports",
        json={"format": "urls", "url": "https://lists.example/engineering"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Collection could not be fetched (http_error)."
    assert "private-secret" not in response.text
    api.command.assert_not_called()


def candidate_detail(identifier, *, selected=None, approval="pending", status="pending"):
    return imports.ImportCandidateDetail(
        id=identifier,
        name="Engineering publisher",
        identity_url="https://publisher.example/",
        status=status,
        approval_status=approval,
        selected_feed_id=selected,
        source_id=identifier,
        updated_at=NOW,
        feeds=[
            imports.ImportFeedOut(
                id=selected,
                url="https://publisher.example/feed",
                evidence=imports.ImportFeedEvidence(
                    title="Engineering",
                    usable_entries=5,
                    publisher_type="direct",
                    publisher_ratio=1.0,
                    recent=True,
                ),
                checked_at=NOW,
            )
        ]
        if selected
        else [],
        assessments=[],
        jobs=[],
        provenance=[
            imports.ImportProvenanceOut(origin="admin:editor:Engineering list", created_at=NOW)
        ],
    )


@pytest.mark.parametrize("action", ["discover", "assess", "select", "reject"])
def test_import_actions_forward_only_explicit_processing_or_attributed_review_intent(
    api, monkeypatch, action
):
    identifier, feed_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(imports, "get_settings", lambda: configuration(ai_enabled=True))
    detail = candidate_detail(
        identifier,
        selected=feed_id if action in {"assess", "select"} else None,
        approval="rejected" if action == "reject" else "pending",
        status="rejected"
        if action == "reject"
        else "ready"
        if action in {"assess", "select"}
        else "pending",
    )
    if action in {"discover", "assess"}:
        detail.jobs = [
            imports.ImportJobOut(
                id=uuid.uuid4(),
                stage=action,
                status="queued",
                attempts=0,
                error=None,
                available_at=NOW,
                lease_until=None,
            )
        ]
    command = Mock()
    name = "select_feed" if action == "select" else "reject" if action == "reject" else "request"
    monkeypatch.setattr(imports.discovery, name, command)
    monkeypatch.setattr(imports.discovery, "show", lambda _: detail)
    body = {
        "action": action,
        **({"feed_id": str(feed_id)} if action == "select" else {}),
        **({"note": "Outside our developer audience"} if action == "reject" else {}),
    }
    response = api.client.post(f"/v1/admin/source-imports/{identifier}/review", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(identifier)
    if action == "select":
        command.assert_called_once_with(identifier, feed_id)
    elif action == "reject":
        command.assert_called_once_with(
            identifier, api.admin.subject, "Outside our developer audience"
        )
    else:
        command.assert_called_once_with(identifier, action, background=True)


@pytest.mark.parametrize(
    "body,message",
    [
        ({"action": "select"}, "Select a feed"),
        ({"action": "approve"}, "Add your review notes"),
        ({"action": "reject"}, "Add your review notes"),
        ({"action": "assess"}, "AI is not enabled"),
    ],
)
def test_import_actions_require_feed_selection_review_notes_or_enabled_ai(
    api, monkeypatch, body, message
):
    commands = [Mock() for _ in range(4)]
    for name, command in zip(
        ("select_feed", "approve", "reject", "request"), commands, strict=True
    ):
        monkeypatch.setattr(imports.discovery, name, command)
    response = api.client.post(f"/v1/admin/source-imports/{uuid.uuid4()}/review", json=body)
    assert response.status_code == (409 if body["action"] == "assess" else 422)
    assert message in response.json()["detail"]
    assert all(not command.called for command in commands)


def test_feed_selection_failure_keeps_remote_diagnostics_private(api, monkeypatch):
    command = Mock(side_effect=FeedError("signed-url?token=private-secret", reason="missing_body"))
    monkeypatch.setattr(imports.discovery, "select_feed", command)
    shown = Mock()
    monkeypatch.setattr(imports.discovery, "show", shown)
    response = api.client.post(
        f"/v1/admin/source-imports/{uuid.uuid4()}/review",
        json={"action": "select", "feed_id": str(uuid.uuid4())},
    )
    assert (
        response.status_code == 422
        and response.json()["detail"] == "Feed could not be validated (missing_body)."
    )
    assert "private-secret" not in response.text
    shown.assert_not_called()


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (25, -1)])
def test_import_pagination_bounds_fail_before_querying_candidates(api, limit, offset):
    response = api.client.get("/v1/admin/source-imports", params={"limit": limit, "offset": offset})
    assert response.status_code == 422 and response.json()["detail"] == "Invalid pagination"
    api.session.execute.assert_not_called()
    api.session.scalar.assert_not_called()
