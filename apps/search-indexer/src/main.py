"""Console entrypoint for the dedicated search indexer runtime."""

import argparse

from devfeed_search_indexer.runtime import run_indexer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consume the durable Typesense search outbox")
    parser.add_argument(
        "--once", action="store_true", help="Process one batch and exit with its result"
    )
    args = parser.parse_args(argv)
    return run_indexer(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
