"""Installed imports and entry points must work outside the checkout directory."""

import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest


def test_package_names_and_queued_task_paths_resolve_outside_checkout(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from importlib import import_module
from importlib.metadata import distribution
from pathlib import Path
from rq.utils import import_attribute

names = ('devfeed_core', 'devfeed_api', 'devfeed_admin_api', 'devfeed_aggregator',
         'devfeed_notifications', 'devfeed_cli', 'devfeed_search_indexer',
         'devfeed_article_enrichment_worker', 'devfeed_images_worker',
         'devfeed_source_discovery_worker')
locations = set()
for name in names:
    package = import_module(name)
    assert package.__name__ == name
    locations.add(Path(package.__file__).resolve().parent)
assert len(locations) == len(names)

assert callable(import_attribute('devfeed_api.main.app'))
assert callable(import_attribute('devfeed_admin_api.main.app'))
assert callable(import_attribute('devfeed_core.feeds.fetcher.fetch_feed'))
assert callable(import_attribute('devfeed_notifications.delivery.deliver_notification'))
assert callable(import_attribute('devfeed_search_indexer.runtime.run_indexer'))
assert callable(import_attribute('devfeed_article_enrichment_worker.main.main'))
assert callable(import_attribute('devfeed_images_worker.main.main'))
assert callable(import_attribute('devfeed_source_discovery_worker.main.main'))
for task in ('tasks.ingest', 'image_tasks.enrich_image',
             'source_tasks.enrich_source', 'article_tasks.enrich_article'):
    handler = import_attribute('devfeed_aggregator.' + task)
    assert callable(handler)
    assert handler.__module__ == 'devfeed_aggregator.' + task.split('.')[0]

for project, command in (('devfeed-cli', 'devfeed'),
                         ('devfeed-aggregator', 'devfeed-worker'),
                         ('devfeed-aggregator', 'devfeed-scheduler'),
                         ('devfeed-search-indexer', 'devfeed-search-indexer'),
                         ('devfeed-article-enrichment-worker', 'devfeed-article-enrichment-worker'),
                         ('devfeed-images-worker', 'devfeed-images-worker'),
                         ('devfeed-source-discovery-worker', 'devfeed-source-discovery-worker')):
    entry, = (item for item in distribution(project).entry_points
              if item.group == 'console_scripts' and item.name == command)
    assert callable(entry.load())
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "devfeed",
        "devfeed-worker",
        "devfeed-scheduler",
        "devfeed-search-indexer",
        "devfeed-article-enrichment-worker",
        "devfeed-images-worker",
        "devfeed-source-discovery-worker",
    ],
)
def test_console_help_works_without_starting_services(command, tmp_path):
    result = subprocess.run(
        [str(Path(sys.executable).with_name(command)), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize(
    ("module", "queue"),
    [
        ("devfeed_article_enrichment_worker.main", "article-enrichment"),
        ("devfeed_images_worker.main", "images"),
        ("devfeed_source_discovery_worker.main", "source-discovery"),
    ],
)
def test_dedicated_worker_apps_pin_their_queue(module, queue, monkeypatch):
    app = import_module(module)
    calls = []
    monkeypatch.setattr(app, "queue_worker_main", lambda *args: calls.append(args) or 0)

    assert app.main() == 0
    assert calls == [(queue, f"Run the {queue} worker")]
