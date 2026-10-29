"""Source actions remain usable after a queued logo job is delivered to RQ."""

import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import ArticleImageJob, utcnow

from tests.admin.sources.test_source_pipeline import PATH, all_cards, detail_path, seed_source
from tests.support.analytics_cards import card_publication as card_publication

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["source-logo", "source-logo-refresh"])
def test_source_board_action_recovers_after_logo_dispatch(
    admin_client, database, operation, card_publication
):
    with database.begin() as session:
        source = seed_source(session)
        job = ArticleImageJob(source_id=source.id, operation=operation)
        session.add(job)
        session.flush()
        identifier, job_id = source.id, job.id
    card_publication(database)
    before = next(
        card for card in all_cards(admin_client.get(PATH).json()) if card["id"] == str(identifier)
    )
    assert before["can_advance"] and before["next_action"] == "profile"

    queue = get_queue("images")
    assert dispatch_jobs(database, queue, 1, utcnow(), kind="images", job_id=job_id) == 1
    assert [job.args[0] for job in queue.jobs] == [str(job_id)]
    with database() as session:
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued" and job.dispatched_at is not None

    path = detail_path(identifier)
    stale = admin_client.post(path + "/advance", json={"expected_snapshot": before["snapshot"]})
    assert stale.status_code == 409
    card_publication(database)
    board = admin_client.get(PATH).json()
    # Publishing cards does not acknowledge a complete reconciliation cycle.
    # Existing cards and revision-checked actions remain usable while it catches up.
    assert board["snapshot"]["status"] == "catching_up"
    assert board["snapshot"]["generated_at"] is None
    assert board["snapshot"]["coverage_expires_at"] is None
    current = next(card for card in all_cards(board) if card["id"] == str(identifier))
    assert current["can_advance"] and current["next_action"] == "profile"
    response = admin_client.post(path + "/advance", json={"expected_snapshot": current["snapshot"]})
    assert response.status_code == 200, response.text
    assert current["snapshot"] != before["snapshot"]
