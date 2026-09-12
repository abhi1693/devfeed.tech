import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_core.article_jobs import claim_article
from devfeed_core.image_jobs import claim_image
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.jobs import claim_job, owned_job
from devfeed_core.models import (
    Article,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    utcnow,
)
from devfeed_core.source_enrichment import claim_enrichment
from sqlalchemy.dialects import postgresql

CASES = [
    (IngestionJob, claim_job),
    (ArticleImageJob, claim_image),
    (SourceEnrichmentJob, claim_enrichment),
    (ArticleEnrichmentJob, claim_article),
]


@pytest.mark.parametrize("model,claim", CASES)
def test_targeted_claim_waits_for_dispatch_lock(model, claim):
    identifier, statements = uuid.uuid4(), []

    def scalar(statement):
        statements.append(statement.compile(dialect=postgresql.dialect()))
        return None

    assert claim(SimpleNamespace(scalar=scalar), identifier) is None
    statement = statements[0]
    assert f"{model.__tablename__}.id =" in str(statement)
    assert identifier in statement.params.values()
    assert "FOR UPDATE" in str(statement)
    assert "SKIP LOCKED" not in str(statement) and "NOWAIT" not in str(statement)


@pytest.mark.parametrize("model,claim", CASES)
def test_claim_rechecks_state_and_duplicate_delivery_does_not_replace_lease(model, claim):
    source = Source(id=uuid.uuid4(), approval_status="approved", enabled=True)
    article = Article(
        id=uuid.uuid4(),
        canonical_url="https://example.com/article",
        metadata_source_type="aggregator",
        image_url=None,
    )
    kwargs = (
        {"source_id": source.id}
        if model in {IngestionJob, SourceEnrichmentJob}
        else {"article_id": article.id}
    )
    job = model(id=uuid.uuid4(), status="queued", attempts=0, available_at=utcnow(), **kwargs)
    session = SimpleNamespace(
        scalar=lambda stmt: source if stmt.column_descriptions[0]["entity"] is Source else job,
        get=lambda entity, *a, **k: article if entity is Article else source,
        scalars=lambda _: [source.id],
    )
    claimed = claim(session, job.id)
    assert claimed[0] is job and job.status == "running" and job.attempts == 1
    token, deadline = job.lease_token, job.lease_until
    assert token and deadline > utcnow()
    assert claim(session, job.id) is None
    assert job.lease_token == token and job.lease_until == deadline and job.attempts == 1


@pytest.mark.parametrize("model,claim", CASES)
@pytest.mark.parametrize("status", ["succeeded", "failed", "delayed"])
def test_waiting_claim_still_ignores_finished_or_not_yet_due_work(model, claim, status):
    job = model(
        status="queued" if status == "delayed" else status,
        attempts=0,
        available_at=utcnow() + timedelta(minutes=5),
    )
    assert claim(SimpleNamespace(scalar=lambda _: job), uuid.uuid4()) is None
    assert job.attempts == 0 and job.lease_token is None


@pytest.mark.parametrize("model", [definition.model for definition in JOB_DEFINITIONS.values()])
@pytest.mark.parametrize(
    "state", ["owned", "reclaimed", "queued", "succeeded", "failed", "deleted"]
)
def test_only_current_running_owner_can_apply_results_or_failures(model, state):
    identifier, token = uuid.uuid4(), uuid.uuid4()
    job = model(
        id=identifier,
        status=state if state in {"queued", "succeeded", "failed"} else "running",
        lease_token=uuid.uuid4() if state == "reclaimed" else token,
        attempts=1,
    )
    before = job.status, job.lease_token, job.attempts
    statements = []

    def scalar(statement):
        statements.append(statement.compile(dialect=postgresql.dialect()))
        if model is IngestionJob and statement.column_descriptions[0]["entity"] is Source:
            return None if state == "deleted" else Source(id=uuid.uuid4())
        return None if state == "deleted" else job

    result = owned_job(SimpleNamespace(scalar=scalar), model, identifier, token)
    assert result is (job if state == "owned" else None)
    assert (job.status, job.lease_token, job.attempts) == before
    assert len(statements) == (2 if model is IngestionJob and state != "deleted" else 1)
    if model is IngestionJob:
        assert "FROM sources" in str(statements[0])
    assert identifier in statements[0].params.values()
    assert "FOR UPDATE" in str(statements[0])
    assert "SKIP LOCKED" not in str(statements[0]) and "NOWAIT" not in str(statements[0])
