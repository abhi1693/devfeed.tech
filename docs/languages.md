# Article language detection

`Article.language` describes the article, not the publication or the community
sharing it. A DEV.to feed declaring English can contain Japanese articles. The
source profile and `origins[].source_metadata.language` continue to preserve the
declared source language independently.

The [editorial analysis layer](editorial.md) can now set canonical language from
validated AI output or an operator correction. The legacy maintenance command
only scans unpublished articles without AI/manual classification; it cannot
overwrite reviewed language metadata or make a published article incomplete.

New publisher articles are detected in the existing RQ ingestion job. Parsing and
source validation do not perform inference. Detection finishes before the write
transaction starts, so no database connection is held while models initialize or
classify text. Retries use the normal durable ingestion job lifecycle. A 304 does
not run detection, and already-imported entries keep their existing metadata;
use the backfill for historical corrections.

The worker uses [Lingua](https://github.com/pemistahl/lingua-py), with all supported
languages instead of a publication-specific allowlist. Its models are installed
with the package and loaded lazily per process; no external service, API key,
runtime download or environment setting is required. Install the changed lockfile
with `uv sync --all-packages --locked` and reload existing worker processes when
ready. Source profiles, the API response shape, and the database schema do not
require a migration for this change.

## Inference policy

- Prefer the publisher excerpt when it contains at least 100 letters. Otherwise,
  combine the publisher title and excerpt; require at least 40 letters total.
- Bound the sample to 2,500 characters. Remove URLs, email addresses, Markdown
  code and any remaining HTML code/invisible elements before detection. Existing
  plain-text excerpts may already have lost code boundaries; this is not a
  full article-body or programming-language classifier.
- Require a top relative score of at least 0.80 and a margin of 0.20 over the
  runner-up. Weak, ambiguous and insufficient text returns null, not English.
  Relative scores are not calibrated probabilities or accuracy guarantees.
- Emit lowercase ISO 639-1 base codes such as `en`, `ja`, `fr` or `zh`. Regional
  variants cannot be inferred reliably and are not fabricated. After backfilling,
  filter with `language=en`, not `language=en-us`.
- Aggregator submission titles/descriptions are not trusted publisher text.
  [Article enrichment](articles.md) fetches the original page separately and runs
  this detector on its readable body, falling back to a meaningful public page
  description. HTML language hints never override detection. Failed/empty lookups
  can leave language unknown.
- This is single predominant-language inference from available text, not a list
  of every language used in mixed-language articles. Unsupported languages and
  code-heavy excerpts can remain unknown or be misclassified; no model is perfect.

## Correct existing records

```sh
uv run devfeed articles detect-languages --limit 100 --dry-run
uv run devfeed articles detect-languages --limit 100
```

The maintenance command uses the worker's exact inference policy, runs locally,
and does not start services or fetch feeds. It scans both null and previously
assigned languages: inherited `en` can become `ja`; `en-us` can become `en`;
unsubstantiated hints can become null. Preview before applying.
Existing non-null `page` results are retained: the page worker saw body text that
is stronger evidence than the stored short preview. The result reason is
`page_body_already_detected`. To re-detect such an article from its page, use
`devfeed articles enrich ARTICLE_UUID --force` instead.

Use `--source-id SOURCE_UUID` to scope by origin, and `--after NEXT_AFTER_UUID`
to resume when the result contains a non-null `next_after`. Keep filters consistent
between batches. Each batch is at most 500 records (100 by default), read into a
snapshot before inference. Writes compare the snapshot's title, summary, metadata
source type and old language with current values, skipping concurrent changes
instead of overwriting them. Retry skipped records in a later pass.

Only changed article language fields are updated. Publication/discovery/feed dates,
URLs, images, descriptions, taxonomy, source profiles and raw origin evidence are
untouched. A successful write transaction invalidates the public GET cache through
the normal post-commit hook; dry runs and unchanged passes do not invalidate it.
