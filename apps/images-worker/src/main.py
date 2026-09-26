"""Run only the durable images queue."""

from devfeed_aggregator.worker import queue_worker_main


def main() -> int:
    return queue_worker_main("images", "Run the images worker")


if __name__ == "__main__":
    raise SystemExit(main())
