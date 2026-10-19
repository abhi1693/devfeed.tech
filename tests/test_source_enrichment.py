"""Profile evidence must belong to the current source and explicit review request."""

from datetime import timedelta

import pytest
from devfeed_core.models import Source, SourceEnrichmentJob, utcnow
from devfeed_core.source_enrichment import profile_current, source_identity


@pytest.mark.parametrize("reopened", [False, True])
def test_missing_profile_never_satisfies_source_admission(reopened):
    source = Source(
        approval_status="pending",
        processing_request=(
            {"kind": "reopen_review", "requested_at": utcnow().isoformat()} if reopened else {}
        ),
    )

    assert profile_current(source, None) is False


@pytest.mark.parametrize("age_seconds,expected", [(None, False), (-1, False), (0, True), (1, True)])
def test_reopened_source_needs_profile_created_at_or_after_review_request(age_seconds, expected):
    requested_at = utcnow()
    source = Source(
        feed_url="https://publisher.example/feed",
        website_url="https://publisher.example/",
        source_type="publisher",
        approval_status="pending",
        processing_request={"kind": "reopen_review", "requested_at": requested_at.isoformat()},
    )
    profile = SourceEnrichmentJob(
        operation="profile",
        status="succeeded",
        result={"input": source_identity(source)},
        created_at=(
            requested_at + timedelta(seconds=age_seconds) if age_seconds is not None else None
        ),
    )

    assert profile_current(source, profile) is expected
