"""Real TCP MCP -> public API -> disposable PostgreSQL/Redis/Typesense."""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from devfeed_api.main import create_app as create_public_api
from devfeed_core.models import Source
from devfeed_core.search_index import backfill, sync_batch
from devfeed_mcp.config import Settings
from devfeed_mcp.main import create_app
from mcp import Client
from test_publication_api import record

pytestmark = pytest.mark.integration


@contextmanager
def serve(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "Server failed to start"
                time.sleep(0.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Server did not shut down"


def test_sdk_calls_all_tools_and_cannot_retrieve_hidden_records(
    database, search_engine, publish_for_read_test
):
    article = record(slug="angular-routing")
    article.origins[0].source.slug = "publisher"
    article_id, source_id = article.id, article.origins[0].source.id
    with database.begin() as session:
        session.add(article)
    publish_for_read_test([article_id])
    backfill(database)
    while sync_batch(database, search_engine):
        pass

    async def scenario(url):
        async with Client(url + "/mcp") as client:

            async def read(name, **arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            feed = await read("get_feed", limit=1, languages=["en"], content_type="tutorial")
            assert [item["id"] for item in feed["items"]] == [str(article_id)]
            detail = await read("get_article", article_id=str(article_id))
            assert detail["canonical_url"] == "https://example.com/post"
            assert detail["content_scope"] == "metadata_and_preview"
            topics = await read("list_topics", has_articles=True)
            assert len(topics["items"]) == 1
            topic = topics["items"][0]
            by_slug = await read("get_feed", topic=topic["slug"])
            by_id = await read("get_feed", topic=topic["id"])
            assert by_id == by_slug
            assert [item["id"] for item in by_id["items"]] == [str(article_id)]
            sources = await read("list_sources", query="Publisher", source_type="publisher")
            assert [item["id"] for item in sources["items"]] == [str(source_id)]
            source = await read("get_source", source_id="publisher")
            assert source["id"] == str(source_id)
            matches = await read("search", query="Angular", section="articles")
            assert [item["id"] for item in matches["sections"]["articles"]["items"]] == [
                str(article_id)
            ]

            # Leave the search index stale: authoritative public reads must still hide the data.
            with database.begin() as session:
                session.get(Source, source_id).approval_status = "rejected"
            for name, arguments in (
                ("get_article", {"article_id": str(article_id)}),
                ("get_source", {"source_id": str(source_id)}),
            ):
                assert (await client.call_tool(name, arguments)).is_error
            assert (await read("get_feed"))["items"] == []
            assert (await read("get_feed", topic=topic["id"]))["items"] == []
            assert (await read("list_sources"))["items"] == []
            matches = await read("search", query="Angular", section="articles")
            assert matches["sections"]["articles"]["items"] == []

    with (
        serve(create_public_api()) as api_url,
        serve(create_app(Settings(api_url=api_url))) as mcp_url,
    ):
        asyncio.run(scenario(mcp_url))
