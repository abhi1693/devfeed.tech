"""Latency resolution must remain finer than the benchmark's regression budgets."""

RESPONSE_TIME_BUCKET_MS = 1


def bucket_response_time(response_time: int | float) -> int:
    # Locust's default changes from 1 ms to 10 ms bins at 100 ms, then 100 ms
    # bins at 1 s. Those discontinuities can invent a >20 ms regression or hide
    # a >2 s smoke-budget violation. Use Locust's supported fixed-width hook.
    return round(response_time)
