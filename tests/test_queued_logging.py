"""A blocked stderr collector cannot block API logging or lose request context."""

import asyncio
import json
import logging
import threading

from devfeed_core.logging import JsonFormatter, QueuedStderrHandler, log_context


def test_api_logging_is_bounded_nonblocking_and_keeps_safe_context(monkeypatch):
    release = threading.Event()
    lines = []
    handler = QueuedStderrHandler(capacity=2)
    handler.setFormatter(JsonFormatter("user-api"))
    logger = logging.Logger("devfeed_user_api.tests", level=logging.INFO)
    logger.addHandler(handler)

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        loop_thread = threading.get_ident()

        class SlowOutput:
            def write(self, line):
                assert threading.get_ident() != loop_thread
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
                lines.append(line)

            def flush(self):
                pass

        monkeypatch.setattr("sys.stderr", SlowOutput())
        with log_context(request_id="first", request_url="https://app.test/?token=private"):
            logger.info("request_completed")
        await asyncio.wait_for(started.wait(), timeout=1)
        with log_context(request_id="second"):
            logger.info("request_completed")
            logger.info("request_completed")
            logger.info("request_completed")  # Capacity exhausted; must not block.
        assert handler.pending.qsize() == 2
        assert handler.dropped == 1
        release.set()
        await asyncio.to_thread(handler.flush)
        assert len(lines) == 3
        first, second, _ = [json.loads(line) for line in lines]
        assert first["event"] == "request_completed"
        assert first["request_id"] == "first" and second["request_id"] == "second"
        assert "private" not in lines[0] and "[redacted]" in lines[0]

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        handler.close()
