"""Two-product preview isolation, manual publication and disposable resource ownership."""

import json
import sys
from contextlib import ExitStack, nullcontext
from unittest.mock import Mock

import httpx
import pytest
from devfeed_core.models import Article
from sqlalchemy import select

from tests.support.paths import REPO_ROOT
from tests.tooling.ci.test_ci_gates import load_script


@pytest.fixture
def catalogs(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts/ci"))
    return load_script("product_catalogs")


@pytest.mark.parametrize("ready", [True, False])
def test_search_container_waits_for_health_and_is_always_removed(catalogs, monkeypatch, ready):
    docker = Mock(side_effect=["owned", "127.0.0.1:18108", ""])
    monkeypatch.setattr(catalogs, "docker", docker)
    response = Mock()
    response.json.return_value = {"ok": ready}
    probe = Mock(side_effect=[httpx.ConnectError("starting"), *([response] * 59)])
    monkeypatch.setattr(catalogs.httpx, "get", probe)
    monkeypatch.setattr(catalogs.time, "sleep", lambda _: None)
    expectation = nullcontext() if ready else pytest.raises(RuntimeError, match="become ready")
    with expectation, catalogs.search_service("hardwaredaily") as env:
        assert env["DEVFEED_SEARCH_URL"] == "http://127.0.0.1:18108"
        assert env["DEVFEED_SEARCH_COLLECTION_PREFIX"] == "hardwaredaily"
        assert env["DEVFEED_SEARCH_ADMIN_KEY"] != env["DEVFEED_SEARCH_QUERY_KEY"]
    docker.assert_called_with("rm", "-f", "owned")


@pytest.mark.integration
@pytest.mark.parametrize("product", ["hardwaredaily", "devfeed"])
def test_candidate_imports_one_unpublished_article(catalogs, database, product, capsys):
    catalogs.prepare_candidate(product)
    with database() as session:
        article = session.scalars(select(Article)).one()
        assert str(article.id) == capsys.readouterr().out.strip()
        assert article.title == catalogs.TITLES[product]
        assert article.publication_status == "unpublished"


def test_deployment_publishes_before_serving_with_query_credentials(
    catalogs, monkeypatch, tmp_path
):
    monkeypatch.setattr(catalogs, "disposable_services", lambda: nullcontext({}))
    monkeypatch.setattr(
        catalogs, "search_service", lambda _: nullcontext({"DEVFEED_SEARCH_ADMIN_KEY": "admin"})
    )
    monkeypatch.setattr(catalogs, "port", Mock(side_effect=[8010, 3010, 8011]))
    command = Mock(side_effect=["", "import log\narticle-id", "", "", "", ""])
    monkeypatch.setattr(catalogs.subprocess, "check_output", command)
    process = Mock(side_effect=lambda *args, **kwargs: nullcontext(args[3]))
    monkeypatch.setattr(catalogs, "process", process)
    health = Mock()
    monkeypatch.setattr(catalogs, "wait_ready", health)
    with ExitStack() as stack:
        api, web = catalogs.deployment(stack, "hardwaredaily", tmp_path)
    commands = [call.args[0] for call in command.call_args_list]
    assert [args[1:] for args in commands[2:5]] == [
        ("articles", "approve", "article-id"),
        ("articles", "publish", "article-id"),
        ("search", "setup"),
    ]
    assert commands[-1][-2:] == ("devfeed_search_indexer.main", "--once")
    assert [call.args[0] for call in health.call_args_list] == [
        api + "/health/ready",
        "http://127.0.0.1:8011/health/ready",
        web + "/login",
    ]
    for call in process.call_args_list:
        env = call.args[1]
        assert env["DEVFEED_PRODUCT"] == "hardwaredaily"
        assert "DEVFEED_SEARCH_ADMIN_KEY" not in env


@pytest.mark.parametrize("contaminated", [False, True])
def test_preview_checks_own_titles_in_feed_search_and_browser(catalogs, monkeypatch, contaminated):
    monkeypatch.setattr(sys, "argv", ["product_catalogs.py"])
    monkeypatch.setattr(
        catalogs,
        "deployment",
        lambda stack, product, path: ("http://" + product, "http://web-" + product),
    )

    def get(url):
        product = httpx.URL(url).host
        items = [{"title": catalogs.TITLES[product]}]
        if contaminated:
            items.append({"title": "Another catalog"})
        body = (
            {"items": items}
            if url.endswith("/feed")
            else {"sections": {"articles": {"items": items}}}
        )
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(catalogs.httpx, "get", get)
    browser = Mock()
    monkeypatch.setattr(catalogs.subprocess, "run", browser)
    with pytest.raises(AssertionError) if contaminated else nullcontext():
        catalogs.main()
    if contaminated:
        browser.assert_not_called()
    else:
        for product, call in zip(catalogs.TITLES, browser.call_args_list, strict=True):
            assert call.args[0][2:4] == ["http://web-" + product, product]
            assert json.loads(call.args[0][4]) == catalogs.TITLES
            assert call.kwargs["check"]
