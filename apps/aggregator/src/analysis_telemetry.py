"""Persist cumulative attempt usage without counting repeated stream updates twice."""

import time

from sqlalchemy import select


def record_attempt(factory, model, identifier, client, started, *, attempt):
    with factory.begin() as session:
        job = session.scalar(select(model).where(model.id == identifier).with_for_update())
        if job is None:
            return
        usage = dict(job.usage or {})
        # A late old worker cannot overwrite or double-count a newer attempt.
        attempts = dict(usage.get("attempts", {}))
        key = str(attempt)
        if key in attempts or attempt <= usage.get("last_recorded_attempt", 0):
            return
        counts = dict(getattr(client, "usage", {}))
        duration = max(0, int((time.perf_counter() - started) * 1000))
        attempts[key] = {"tokens": counts, "duration_ms": duration}
        for name, value in counts.items():
            usage[name] = usage.get(name, 0) + value
        retained = {key: attempts[key] for key in sorted(attempts, key=int)[-20:]}
        job.usage = {**usage, "attempts": retained, "last_recorded_attempt": attempt}
        job.duration_ms = (job.duration_ms or 0) + duration
