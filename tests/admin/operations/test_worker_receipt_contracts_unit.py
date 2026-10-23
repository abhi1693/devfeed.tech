"""Operator snapshots survive independent transport, job and subject retention."""

import asyncio
import json
import uuid
import zlib
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import codex_connection, workers
from devfeed_core.models import Article, ArticleEnrichmentJob
from devfeed_core.urls import fingerprint

from tests.admin.operations.test_codex_protocol_guard_policy_unit import monitor
from tests.support.source_readiness import NOW, job


def transport(identifier):
    connection, pipe = Mock(), Mock()
    connection.pipeline.return_value.__enter__ = Mock(return_value=pipe)
    connection.pipeline.return_value.__exit__ = Mock(return_value=False)
    raw = zlib.compress(
        json.dumps(
            ["devfeed_aggregator.article_tasks.enrich_article", None, [str(identifier)], {}]
        ).encode()
    )
    pipe.execute.return_value = [(raw, (NOW - timedelta(seconds=12)).isoformat().encode())]
    return connection, pipe


def test_expired_durable_job_leaves_the_current_transport_receipt_visible_without_inventing_a_run():
    connection, pipe = transport(uuid.uuid4())
    session = Mock()
    session.scalars.return_value = []
    output = workers.current_jobs(connection, session, ["rq-retained"], NOW)["rq-retained"]
    assert output.rq_id == "rq-retained" and output.elapsed_seconds == 12
    assert output.started_at == NOW - timedelta(seconds=12)
    assert output.id is output.kind is output.status is output.target_name is None
    pipe.hmget.assert_called_once_with("rq:job:rq-retained", "data", "started_at")
    session.add.assert_not_called()
    connection.delete.assert_not_called()


@pytest.mark.parametrize("retained_subject", [False, True])
def test_job_loaded_before_a_subject_deletion_retains_run_identity_without_guessing_a_title(
    retained_subject,
):
    identifier = uuid.uuid4()
    current = job(
        ArticleEnrichmentJob,
        status="running",
        article_id=identifier,
        result={"private": "saved extraction evidence"},
    )
    url = "https://publisher.example/angular-router-guards"
    article = Article(
        id=identifier,
        canonical_url=url,
        url_hash=fingerprint(url),
        slug="angular-router-guards",
        title="Angular router guards",
        discovered_at=NOW - timedelta(hours=2),
        feed_at=NOW - timedelta(hours=2),
        review_status="pending",
        publication_status="unpublished",
        editorial_revision=0,
    )
    connection, _ = transport(current.id)
    session = Mock()
    # These are independent read outcomes. A cascade can occur after the job
    # read and before the subject lookup; SQL membership is not simulated.
    rows = {ArticleEnrichmentJob: [current], Article: [article] if retained_subject else []}
    session.scalars.side_effect = lambda statement: rows.get(
        statement.column_descriptions[0]["entity"], []
    )
    output = workers.current_jobs(connection, session, ["rq-current"], NOW)["rq-current"]
    assert (output.id, output.kind, output.status, output.article_id) == (
        current.id,
        "article-enrichment",
        "running",
        identifier,
    )
    assert output.target_name == (article.title if retained_subject else None)
    assert "saved extraction evidence" not in output.model_dump_json()
    session.commit.assert_not_called()


@pytest.mark.parametrize("previous_identity", [False, True])
def test_provider_without_a_chatgpt_account_requirement_is_connected_without_a_quota_lookup(
    previous_identity,
):
    async def run():
        client, ws = monitor([{"id": 1, "result": {"account": None, "requiresOpenaiAuth": False}}])
        if previous_identity:
            client.status.email, client.status.plan = "previous@example.com", "old plan"
            client.status.quota = [codex_connection.CodexQuota(used_percent=100, window_minutes=60)]
        await client.check()
        assert client.status.state == "connected"
        assert "does not require a ChatGPT account" in client.status.message
        assert client.status.email is client.status.plan is None and client.status.quota == []
        assert client.ws is ws
        assert [json.loads(call.args[0])["method"] for call in ws.send.call_args_list] == [
            "account/read"
        ]
        ws.close.assert_not_awaited()

    asyncio.run(run())
