# Published reader sample

`published.json` is a public DevFeed snapshot captured on September 13, 2026. It contains
137 articles across all six content types, 53 sources, 97 topics, and 652 tags. Titles,
summaries, image URLs, canonical links, topic classifications, and publication dates come
from the public feed and topic catalogue. It contains no accounts, sessions, credentials,
private editorial records, or full article bodies. Images remain linked to their publishers.

With the local Compose stack running, from the repository root:

```sh
npm run dev:seed
# Equivalent; only Python 3 and Docker are needed on the host:
python3 scripts/seed_dev.py
```

Open <http://localhost:3000> to browse the sample. The script uses the running `api`
container's Python dependencies and checks that its database connection reaches this
checkout's local Compose PostgreSQL container and the `devfeed` database. Remote Docker
contexts and external database overrides are rejected. Start the stack using
[the Compose guide](../../docs/compose.md) first; migrations must be current.

The entire import is transactional. Repeated runs skip existing articles matched by ID,
canonical URL hash, or slug, and reuse existing sources/topics/tags without changing them.
Local edits and publication decisions survive reseeding. Nothing is deleted or reset.
The snapshot loads offline; it does not download content or call AI services.

New sources are approved and enabled for source discovery, with polling deferred until
2100 and reserved
`https://seed.invalid/…` feed URLs because feed configuration is not public. Configure a real
feed explicitly before fetching one. Public topics are active so follows, filters and
previews work. Their automatic relationship research is deferred until 2100; a later topic
edit or explicit research action can reset that deferral. No AI, notification or ingestion
jobs are created by the importer. New tags have automatic topic discovery disabled.
Publication dates are preserved exactly, including any dates before the AI cutoff.

Search events are recorded by the normal database triggers. If the local `search` profile
is enabled, its indexer picks up the sample automatically. The importer invalidates public
response caches after commit. Search remains optional; no private user data or artificial
likes are seeded.

For a separately curated snapshot with the same schema:

```sh
python3 scripts/seed_dev.py --file path/to/published.json
```
