# Aggregator

## Personal feed preparation

The scheduler runs a lightweight recommendation dispatcher independently of its
main ingestion cycle. It checks durable due work every second, prioritizes first
feeds, and enqueues those first builds ahead of routine ingestion. Existing feeds
retain their recurring refresh cadence. Both dispatcher paths use the same row
locks and delivery IDs, so concurrent scheduling coalesces safely; failed dispatch
leaves durable work available for retry.

`recommendation_refresh_dispatched` reports due-to-dispatch `wait_ms`, and
`recommendation_refresh_started` reports `queue_wait_ms`. Completion reports
`duration_ms` and article count in `recommendation_refresh_completed`. These
separate scheduling and queue delays from computation; they are not a promised
user-facing ETA. End-to-end production latency still requires observing these
logs alongside browser requests after deployment.
