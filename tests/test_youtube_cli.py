"""CLI targets keep provider identity and review boundaries during real persistence."""

import json
import uuid

import pytest
from devfeed_aggregator import tasks
from devfeed_cli.main import run
from devfeed_core import services
from devfeed_core.models import Article, IngestionJob, Source
from devfeed_core.schemas import SourceDecision
from sqlalchemy import func, select
from test_youtube_submission import CHANNEL, PLAYLIST, VIDEO, WATCH
from test_youtube_submission import youtube_provider as youtube_provider

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("target", ["channel", "playlist", "video"])
def test_cli_source_add_retains_explicit_youtube_target_and_review(
    database, youtube_provider, capsys, target
):
    url = "@developers" if target == "channel" else WATCH
    assert run(["sources", "add", url, "--type", "publisher", "--youtube-target", target]) == 0
    payload = json.loads(capsys.readouterr().out)
    resource = payload["source"]
    assert resource["provider"] == "youtube" and resource["resource_kind"] == target
    assert (
        resource["provider_resource_id"]
        == {"channel": CHANNEL, "playlist": PLAYLIST, "video": VIDEO}[target]
    )
    assert resource["approval_status"] == "pending" and payload["job"] is None
    assert resource["recurring"] is (target != "video")
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(func.count()).select_from(Article)) == 0
    assert run(["sources", "add", url, "--type", "publisher", "--youtube-target", target]) == 0
    assert json.loads(capsys.readouterr().out)["created"] is False
    if target == "video":
        review_and_import(database, resource["id"])
        with database() as session:
            assert session.scalar(select(Article.provider_resource_id)) == VIDEO
            assert session.scalar(select(func.count()).select_from(Source)) == 1


def test_cli_batch_import_deduplicates_variants_without_importing_playlist(
    database, youtube_provider, tmp_path, capsys
):
    batch = tmp_path / "sources.txt"
    batch.write_text(f"{WATCH}\nhttps://youtu.be/{VIDEO}?t=99\n")
    assert run(["sources", "import", str(batch), "--type", "publisher"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] == 1
    assert {item["source"]["resource_kind"] for item in payload["items"]} == {"video"}
    with database() as session:
        record = session.scalar(select(Source))
        assert record.provider_resource_id == VIDEO and not record.recurring
        source_id = record.id
    review_and_import(database, source_id)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 1


def review_and_import(database, source_id):
    identifier = uuid.UUID(str(source_id))
    with database.begin() as session:
        services.review_source(session, identifier, SourceDecision(decision="approved"))
        job_id = session.scalar(select(IngestionJob.id).where(IngestionJob.source_id == identifier))
    tasks.ingest(str(job_id))
