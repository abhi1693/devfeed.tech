"""Run only the durable article-enrichment queue."""

from devfeed_aggregator.worker import queue_worker_main


def main() -> int:
    return queue_worker_main("article-enrichment", "Run the article-enrichment worker")


if __name__ == "__main__":
    raise SystemExit(main())
