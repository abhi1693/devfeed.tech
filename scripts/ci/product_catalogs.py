"""Prove manual publication and reader/search isolation with disposable product deployments."""

import argparse
import json
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from live_browser import ROOT, port, process, wait_ready
from services import disposable_services, docker

TITLES = {
    "hardwaredaily": "Tracing microcontroller interrupts with a logic analyzer",
    "devfeed": "Tracing database transactions with query logs",
}


@contextmanager
def search_service(product):
    key = secrets.token_hex(24)
    container = docker(
        "run",
        "-d",
        "--rm",
        "-p",
        "127.0.0.1::8108",
        "typesense/typesense:30.2",
        "--data-dir=/tmp",
        f"--api-key={key}",
    )
    try:
        origin = "http://127.0.0.1:" + docker("port", container, "8108/tcp").rsplit(":", 1)[1]
        for _ in range(60):
            try:
                if httpx.get(origin + "/health").json().get("ok"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Disposable Typesense did not become ready")
        yield dict(
            DEVFEED_SEARCH_ENABLED="true",
            DEVFEED_SEARCH_URL=origin,
            DEVFEED_SEARCH_COLLECTION_PREFIX=product,
            DEVFEED_SEARCH_ADMIN_KEY=key,
            DEVFEED_SEARCH_QUERY_KEY=secrets.token_hex(24),
        )
    finally:
        docker("rm", "-f", container)


def prepare_candidate(product):
    from devfeed_aggregator.tasks import store_entries
    from devfeed_core.config import get_settings
    from devfeed_core.db import session_factory
    from devfeed_core.feeds.parser import parse_feed
    from devfeed_core.job_lifecycle import finish_job
    from devfeed_core.models import Article, ArticleImageJob, ArticleTopic, Source, Topic, utcnow
    from devfeed_core.source_types import SourceType
    from sqlalchemy import select
    from sqlalchemy.engine import make_url

    from tests.support.article_processing_helpers import complete_processing

    target = make_url(get_settings().database_url)
    if target.host != "127.0.0.1" or target.database != "devfeed_test":
        raise ValueError("Candidate fixtures require a disposable local test database")
    title = TITLES[product]
    topic_name = "microcontrollers" if product == "hardwaredaily" else "databases"
    url = f"https://example.com/{topic_name}"
    with session_factory().begin() as session:
        source = Source(
            name="Fixture publisher",
            feed_url=url + "/rss",
            approval_status="approved",
            source_type="publisher",
        )
        topic = Topic(name=topic_name, slug=topic_name, kind="technology", status="active")
        session.add(source)
        session.flush()
        body = (
            f'<rss version="2.0"><channel><language>en</language><item><title>{title}</title>'
            f"<link>{url}</link><description>{title}.</description></item></channel></rss>"
        )
        feed = parse_feed(
            body.encode(), source.feed_url, utcnow(), source_type=SourceType.PUBLISHER
        )
        assert store_entries(session, source.id, feed) == 1
        assert store_entries(session, source.id, feed) == 0
        article = session.scalars(select(Article)).one()
        assert article.publication_status == "unpublished"
        article.ai_summary = article.ai_description = article.summary
        article.language = "en"
        article.content_format = "article"
        article.content_type = "tutorial"
        article.classification_provenance = {
            "origin": "manual",
            "developer_relevance": "relevant",
            "page_kind": "article",
        }
        session.add(topic)
        session.flush()
        session.add(
            ArticleTopic(
                article_id=article.id,
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence=title,
                origin="manual",
            )
        )
        image_job = session.scalars(
            select(ArticleImageJob).where(ArticleImageJob.article_id == article.id)
        ).one()
        finish_job(image_job, "not_found", utcnow())
        session.flush()
        complete_processing(session, article)
        print(article.id)


def deployment(stack, product, directory):
    env = stack.enter_context(disposable_services())
    env.update(stack.enter_context(search_service(product)))
    api_port, web_port, user_port = port(), port(), port()
    api, web = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
    env.update(
        DEVFEED_PRODUCT=product,
        DEVFEED_PUBLIC_API_URL=api,
        DEVFEED_USER_BASE_URL=web,
        DEVFEED_USER_API_URL=f"http://127.0.0.1:{user_port}",
        DEVFEED_USER_COOKIE_SECURE="false",
        PYTHONPATH=str(ROOT),
        DEVFEED_ANALYTICS_ENABLED="false",
    )

    def run(*args):
        return subprocess.check_output(args, env=env, cwd=directory, text=True).strip()

    run(sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head")
    identifier = run(sys.executable, __file__, "candidate", product).splitlines()[-1]
    cli = Path(sys.executable).with_name("devfeed")
    for action in ("approve", "publish"):
        run(cli, "articles", action, identifier)
    run(cli, "search", "setup")
    run(sys.executable, "-m", "devfeed_search_indexer.main", "--once")
    del env["DEVFEED_SEARCH_ADMIN_KEY"]
    for name, command, health in (
        (
            "api",
            [sys.executable, "-m", "uvicorn", "devfeed_api.main:app", "--port", str(api_port)],
            api + "/health/ready",
        ),
        (
            "user-api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "devfeed_user_api.main:app",
                "--port",
                str(user_port),
            ],
            f"http://127.0.0.1:{user_port}/health/ready",
        ),
        (
            "web",
            [
                "node",
                str(ROOT / "node_modules/next/dist/bin/next"),
                "start",
                str(ROOT / "apps/web"),
                "--hostname",
                "127.0.0.1",
                "--port",
                str(web_port),
            ],
            web + "/login",
        ),
    ):
        child = stack.enter_context(
            process(
                command,
                env,
                ROOT / "apps/web" if name == "web" else directory,
                product + "-" + name,
                log_directory=directory,
            )
        )
        try:
            wait_ready(health, child)
        except RuntimeError:
            print((directory / f"{product}-{name}.log").read_text()[-3000:], file=sys.stderr)
            raise
    return api, web


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Keep verified local readers running")
    args = parser.parse_args()
    with TemporaryDirectory() as temporary, ExitStack() as stack:
        deployments = {product: deployment(stack, product, Path(temporary)) for product in TITLES}
        for product, (api, web) in deployments.items():
            for path in ("/v1/feed", "/v1/search?q=Tracing"):
                result = httpx.get(api + path).raise_for_status().json()
                articles = (
                    result["items"]
                    if path == "/v1/feed"
                    else result["sections"]["articles"]["items"]
                )
                assert [item["title"] for item in articles] == [TITLES[product]], (
                    product,
                    path,
                    result,
                )
            subprocess.run(
                [
                    "node",
                    str(ROOT / "scripts/testing/product-catalogs.mjs"),
                    web,
                    product,
                    json.dumps(TITLES),
                ],
                check=True,
            )
            print(
                f"{product}: verified feed, search, and reader at {web}/latest",
                flush=True,
            )
        if args.serve:
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    if sys.argv[1:2] == ["candidate"]:
        prepare_candidate(sys.argv[2])
    else:
        main()
