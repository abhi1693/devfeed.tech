# Search

The reader’s search box opens `/search?q=…`. One query searches articles, topics,
sources and tags, with articles first and every section visible. Each section has
its own “More” control. Typo tolerance, prefixes and topic/tag aliases help readers
find a match; title matches rank above descriptions and related taxonomy.

## Local setup

Search uses Typesense 30.2 and PostgreSQL. Add these settings to the ignored `.env`:

```dotenv
COMPOSE_FILE=compose.yaml:compose.build.yaml
COMPOSE_PROFILES=search
DEVFEED_SEARCH_ENABLED=true
DEVFEED_SEARCH_URL=http://typesense:8108
DEVFEED_SEARCH_COLLECTION_PREFIX=devfeed
DEVFEED_SEARCH_ADMIN_KEY=<first-random-key>
DEVFEED_SEARCH_QUERY_KEY=<different-random-key>
DEVFEED_SEARCH_INDEX_BATCH_SIZE=200
```

Generate two separate keys with `openssl rand -hex 32`. Preserve other enabled
profiles: for example, use `COMPOSE_PROFILES=ai,search` when running bundled AI.
Keep the keys out of Git. Then run:

```sh
docker compose up --build -d
```

The build override builds the indexer from the local backend Dockerfile on either
AMD64 or ARM64. Typesense uses a pinned multi-architecture upstream image, an
internal network and the persistent `search-data` volume. No host port is exposed.
The API receives only the query key; the indexer receives the management key and
creates a key restricted to searching the four DevFeed collections.

Migration `0003` creates a durable change queue and queues existing records for
indexing. The `search-indexer` service waits for migrations and healthy Typesense,
creates collections, then consumes pending changes. The initial backfill finishes
in the background; new installations have empty search results until articles are
published and indexed. Search is optional and disabled in the example configuration.

## Data flow and recovery

PostgreSQL remains authoritative. Database triggers record committed article,
source, topic, tag and relationship changes, including bulk updates and cascading
deletes. A dedicated indexer reads small batches independently of AI worker queues.
Content writes never call Typesense. Failed imports remain queued; retries are
idempotent and check each document’s import result before acknowledging a batch.

A single federated Typesense request finds ranked IDs. The API then reads at most
12 current records per section by primary key and rechecks public visibility.
Unpublished articles and unapproved sources are excluded; catalogue entries require
published coverage. Source/topic changes also refresh related article search terms.
Indexing and application writes invalidate cached public responses.

Operational commands:

```sh
docker compose exec search-indexer devfeed search backfill
docker compose exec search-indexer devfeed search worker --once
docker compose logs --tail=50 search-indexer
```

`backfill` queues a fresh projection from PostgreSQL without deleting content. The
worker’s heartbeat health check detects a stalled indexer. Pending events survive
restarts in PostgreSQL; Typesense collections persist in `search-data`. For an empty
replacement index, run setup and backfill. Index data is rebuildable; back up
PostgreSQL and provision the same credentials when restoring services. The current
Compose configuration has one Typesense instance; it does not provide search HA.

## Query budgets and verification

Queries are limited to 200 characters and 20 words. The API controls search fields,
weights, pagination and collection names. Each page has at most 12 hits per section;
pagination stops at 960 hits per section, so broad queries should be refined.
Index requests use a short timeout and an 800 ms engine search cutoff. Database
statements use the remaining three-second handler budget, and the web fetch aborts
after 3.5 seconds. An unavailable index returns a retryable error; it never triggers
a catalogue-wide SQL search. These deadlines bound normal work, not a guarantee
against network delays, overloaded infrastructure or database connection contention.

Search typing is debounced. Client-side search navigation and pagination pause when
the tab is inactive. Search pages are excluded from indexing by search engines.
No management key or raw Typesense response is sent to browsers.

Run a reproducible scale profile with disposable PostgreSQL, Redis and Typesense:

```sh
DEVFEED_PROFILE_SUITE=search DEVFEED_SEARCH_PROFILE_ROWS=100000 \
  bash scripts/profile-api.sh reports/search-profile.json
```

A local run on September 12, 2026 with the fixture below and the response cache
disabled measured 18–221 ms p95 across six query shapes. An eight-client, 48-request
workload measured 262 ms p95 and 346 ms maximum. The initial projection took
85 seconds. These synthetic host measurements are a regression baseline, not a
production latency guarantee.

The report includes cold response-cache timings, an eight-client workload, SQL
counts and `EXPLAIN ANALYZE` plans. The fixture contains 100,000 articles, 2,000
topics, 4,000 tags and 200 sources. Tests also cover typo and alias matches, ranked
results, publication withdrawal before indexing catches up, deletion, rollback,
partial-import failures, independent section pagination and hidden-tab cancellation.

Deployments must apply migration `0003`, provision Typesense and both keys, enable
search on the public API and run the indexer with the same collection prefix. No
production rollout is performed merely by changing the local Compose setup.
