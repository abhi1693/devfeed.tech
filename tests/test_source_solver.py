"""Transient solver transport does not leave queued work behind on timeout."""

import base64
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.feeds import queued_solver as source_solver
from devfeed_core.feeds.fetcher import FeedError
from redis.exceptions import ConnectionError
from rq.job import JobStatus

URL = "https://publication.example/feed"


@pytest.fixture
def queued(monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    job = Mock()
    job.get_status.return_value = JobStatus.FINISHED
    job.return_value.return_value = {"body": base64.b64encode(b"feed").decode(), "url": URL}
    queue = Mock()
    queue.enqueue.return_value = job
    monkeypatch.setattr(source_solver, "create_redis", lambda *a, **kw: nullcontext(object()))
    monkeypatch.setattr(source_solver, "Queue", lambda *a, **kw: queue)
    return SimpleNamespace(job=job, queue=queue)


def test_explicit_request_uses_fixed_worker_target_and_expires(queued):
    result = source_solver.solve_feed(URL)
    assert result.body == b"feed"
    args, kwargs = queued.queue.enqueue.call_args
    assert args == ("devfeed_core.feeds.solvers.solve_feed_request", URL)
    assert kwargs["ttl"] == 15
    assert kwargs["job_timeout"] == 80
    assert kwargs["result_ttl"] == 120
    queued.job.delete.assert_called_once()


def test_timeout_cancels_queued_request(queued, monkeypatch):
    queued.job.get_status.return_value = JobStatus.QUEUED
    monkeypatch.setattr(source_solver.time, "monotonic", Mock(side_effect=[0, 96]))
    with pytest.raises(FeedError, match="did not finish"):
        source_solver.solve_feed(URL)
    queued.job.cancel.assert_called_once()
    queued.job.delete.assert_called_once()


def test_timeout_leaves_running_work_to_its_bounded_worker_timeout(queued, monkeypatch):
    queued.job.get_status.return_value = JobStatus.STARTED
    monkeypatch.setattr(source_solver.time, "monotonic", Mock(side_effect=[0, 96]))
    with pytest.raises(FeedError):
        source_solver.solve_feed(URL)
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_not_called()


def test_solver_errors_do_not_expose_worker_internals(queued):
    queued.job.return_value.return_value = {"error": "internal provider detail"}
    with pytest.raises(FeedError) as error:
        source_solver.solve_feed(URL)
    assert error.value.reason == "solver_unavailable"
    assert "internal" not in str(error.value)


def test_redis_failure_is_actionable(queued):
    queued.queue.enqueue.side_effect = ConnectionError("private connection details")
    with pytest.raises(FeedError, match="solver is unavailable") as error:
        source_solver.solve_feed(URL)
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [{}, {"body": "!", "url": URL}, {"body": [], "url": URL}, {"body": "", "url": None}],
)
def test_invalid_solver_result_is_safe_and_cleaned_up(queued, result):
    queued.job.return_value.return_value = result
    with pytest.raises(FeedError) as error:
        source_solver.solve_image(URL)
    assert error.value.reason == "solver_unavailable"
    queued.job.delete.assert_called_once()


def test_disabled_solver_does_not_enqueue(queued, monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", False)
    with pytest.raises(FeedError, match="not enabled"):
        source_solver.solve_feed(URL)
    queued.queue.enqueue.assert_not_called()


def test_queued_image_returns_binary_and_uses_image_worker_target(queued):
    queued.job.return_value.return_value = {
        "body": base64.b64encode(b"\x89PNG\xff\x00").decode(),
        "url": URL,
        "content_type": "image/png",
    }
    result = source_solver.solve_image(URL)
    assert result.body == b"\x89PNG\xff\x00"
    assert result.content_type == "image/png"
    assert queued.queue.enqueue.call_args.args == (
        "devfeed_core.feeds.solvers.solve_image_request",
        URL,
    )


def test_image_fetch_hands_challenges_to_isolated_solver(monkeypatch, queued):
    from devfeed_core.feeds import fetcher

    def challenge(*args, **kwargs):
        raise FeedError("Challenge", reason="browser_challenge")

    monkeypatch.setattr(fetcher, "_fetch", challenge)
    queued.job.return_value.return_value = {
        "body": base64.b64encode(b"png bytes").decode(),
        "url": URL,
        "content_type": "image/png",
    }
    assert fetcher.fetch_topic_logo(URL).body == b"png bytes"
    queued.queue.enqueue.assert_called_once()
