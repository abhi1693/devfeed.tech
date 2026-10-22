"""Published content and unknown deliveries cannot enter enrichment through manual commands."""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_core import article_jobs
from devfeed_core.services import OperationConflict, RecordNotFound
from test_runtime100_published_thumbnail_unit import published


@pytest.mark.parametrize("automatic", [False, True])
def test_published_article_requires_withdrawal_before_new_enrichment(monkeypatch, automatic):
    article = published(monkeypatch, "v2")
    session = Mock()
    session.scalar.return_value = article
    approved = Mock(
        side_effect=AssertionError("Published content must stop before source admission")
    )
    monkeypatch.setattr(article_jobs, "approved_sources", approved)
    if automatic:
        assert article_jobs.request_article_enrichment(session, article.id, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="Unpublish the article before requesting"):
            article_jobs.request_article_enrichment(session, article.id)
    approved.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert article.publication_status == "published"


@pytest.mark.parametrize("operation", ["retry_article", "prepare_article_dispatch"])
def test_unknown_enrichment_job_returns_a_domain_not_found_without_creating_replacement(operation):
    session = Mock()
    session.get.return_value = session.scalar.return_value = None
    with pytest.raises(RecordNotFound, match="Article enrichment job not found"):
        getattr(article_jobs, operation)(session, uuid.uuid4())
    session.add.assert_not_called()
    session.flush.assert_not_called()
