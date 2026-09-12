"""Job pages must load names without a browser request fan-out or pool starvation."""

import asyncio
import uuid

import httpx
import pytest
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.jobs import jobs
from devfeed_admin_api.pagination import ListQuery
from devfeed_admin_api.sources import detail
from devfeed_core.models import IngestionJob, Source
from devfeed_http.dependencies import session_dependency
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration


def test_job_page_and_source_release_connections_before_serialization(database, admin_client):
    source_id = uuid.uuid4()
    with database.begin() as session:
        session.add(
            Source(
                id=source_id,
                name="Publisher",
                source_type="publisher",
                feed_url="https://publisher.test/feed",
            )
        )
        session.flush()
        session.add(IngestionJob(source_id=source_id))
    with database() as session:
        result = detail(source_id, session)
        assert not session.in_transaction()
        assert result.name == "Publisher"
        result = jobs(
            "ingestion", session, ListQuery(q="", sort=None, offset=0, limit=25), status="queued"
        )
        assert not session.in_transaction()
        assert result["items"][0].target_name == "Publisher"

    # Mirror the production one-connection pool. More requests than the HTTP
    # thread capacity must complete without waiting for a 30-second pool timeout.
    engine = create_engine(database.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=2)
    admin_client.app.dependency_overrides[get_session] = session_dependency(
        lambda: sessionmaker(engine)
    )

    async def burst():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=admin_client.app), base_url="http://testserver"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *[client.get(f"/v1/admin/sources/{source_id}") for _ in range(50)],
                    client.get("/v1/admin/jobs/ingestion?status=queued&limit=25&offset=0"),
                ),
                timeout=15,
            )
            assert all(response.status_code == 200 for response in responses)

    try:
        asyncio.run(burst())
    finally:
        admin_client.app.dependency_overrides.pop(get_session)
        engine.dispose()
