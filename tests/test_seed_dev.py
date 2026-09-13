import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("seed_dev", ROOT / "scripts/seed_dev.py")
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


@pytest.fixture
def sample():
    return json.loads((ROOT / "dev/seed/published.json").read_text())


def test_published_snapshot_has_complete_references_and_all_reader_types(sample):
    assert seed.validate_snapshot(sample) is sample
    assert {a["content_type"] for a in sample["articles"]} == {
        "article",
        "news",
        "tutorial",
        "release",
        "comparison",
        "opinion",
    }
    assert len(sample["articles"]) > 48  # Exercise more than two reader pages.
    assert all(a["canonical_url"].startswith("https://") for a in sample["articles"])


@pytest.mark.parametrize("mutation", ["duplicate", "topic", "publication", "date", "sources"])
def test_invalid_snapshot_is_rejected_before_writes(sample, mutation):
    value = deepcopy(sample)
    if mutation == "duplicate":
        value["articles"].append(value["articles"][0])
    elif mutation == "topic":
        value["topics"] = []
    elif mutation == "publication":
        value["articles"][0]["published_to_feed_at"] = None
    elif mutation == "sources":
        value["articles"][0]["sources"] = []
    else:
        value["articles"][0]["published_at"] = "2026-09-01T00:00:00"
    with pytest.raises(ValueError):
        seed.validate_snapshot(value)


def test_local_target_must_match_the_inspected_compose_server():
    url = make_url("postgresql+psycopg://devfeed@postgres/devfeed")
    seed.validate_target(url, "devfeed", "172.18.0.2", ["172.18.0.2"])
    for changed_url, database, address in [
        (url.set(host="production.example"), "devfeed", "172.18.0.2"),
        (url.set(database="production"), "production", "172.18.0.2"),
        (url, "devfeed", "172.18.0.99"),
        (url, "other", "172.18.0.2"),
    ]:
        with pytest.raises(ValueError, match="local Compose"):
            seed.validate_target(changed_url, database, address, ["172.18.0.2"])
