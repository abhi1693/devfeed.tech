"""Pause a contended worker only after its claim transaction has rolled back."""

import time
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

from devfeed_aggregator import partner_claim
from devfeed_core.partner_catalog import lock_catalog
from sqlalchemy import text


@contextmanager
def paused_claim_retry(monkeypatch, factory):
    entered, resume = Event(), Event()

    def sleep(_seconds):
        entered.set()
        assert resume.wait(timeout=3), "Contended worker retry was not released"

    def assert_waiting(future):
        assert entered.wait(timeout=2), "Worker did not retry its contended claim"
        assert not future.done(), "Worker discarded the still-durable delivery"
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            lock_catalog(session)

    with monkeypatch.context() as patch:
        patch.setattr(partner_claim, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
        try:
            yield SimpleNamespace(assert_waiting=assert_waiting, resume=resume.set)
        finally:
            resume.set()
