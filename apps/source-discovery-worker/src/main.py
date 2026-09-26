"""Run only the durable source-discovery queue."""

from devfeed_aggregator.worker import queue_worker_main


def main() -> int:
    return queue_worker_main("source-discovery", "Run the source-discovery worker")


if __name__ == "__main__":
    raise SystemExit(main())
