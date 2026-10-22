"""Qualified previews and retained evaluation receipts tolerate ordinary boundaries.

Supplied article/catalog reads are already scoped. The retention case models
independent read outcomes, not SQL selection, deletion or transaction isolation.
"""

from datetime import timedelta
from unittest.mock import Mock

from devfeed_core import partner_matching, partner_product_logos
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.job_lifecycle import start_job
from devfeed_http import partner_portal
from sqlalchemy.orm import Session
from test_admin_partner_read_contracts_unit import evaluation
from test_partner_matching_workflow_unit import NOW, article, product
from test_partner_placement_workflow_unit import account


def test_independently_qualified_inventory_preview_does_not_require_trusted_account_acceptance():
    owner, value, session = account(approval_mode="qualified"), product(), Mock(spec=Session)
    session.scalars.return_value = [value]
    selected, destination = partner_portal._preview_product(session, owner)
    assert selected is value
    assert destination == "https://checker.example/?utm_source=devfeed"
    session.get.assert_not_called()
    session.commit.assert_not_called()


def test_http_product_page_retains_declared_https_icon_without_insecure_favicon_fallback():
    response = FetchResult(
        200,
        b'<html><head><template><link rel="icon" href="https://cdn.example/hidden.png">'
        b'</template><link rel="icon" href="https://cdn.example/official.png"></head>'
        b"<body><div>Developer documentation</div></body></html>",
        "http://product.example/docs",
        content_type="text/html",
    )
    assert partner_product_logos.product_icon_urls(response) == ["https://cdn.example/official.png"]


def test_evaluation_application_skips_an_article_purged_after_its_input_fence(monkeypatch):
    value, story, session = product(), article(), Mock(spec=Session)
    job = evaluation(value, story)
    output = job.result
    job.result, job.status, job.attempts, job.finished_at = {}, "queued", 0, None
    start_job(job, NOW - timedelta(seconds=10), 360)
    job.result = output  # Validated provider output is awaiting match application.
    session.get.return_value = value
    # The worker's earlier input fence read this snapshot article. Article
    # retention can finish before the separate application read; the evaluation
    # refers to sampled articles in JSON, without an article foreign key.
    session.scalars.return_value = []
    save = Mock()
    monkeypatch.setattr(partner_matching, "save_article_match", save)
    partner_matching.save_evaluation_matches(session, job)
    save.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert job.status == "running" and job.result == output
    assert job.lease_token is not None and job.lease_until > NOW
