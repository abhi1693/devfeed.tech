# Article title review

Article analysis reviews headlines alongside summaries and classifications. It uses
existing article-analysis workers and their AI capacity limits; no separate inference
call is needed.

Clear source titles are preserved. Vague or sensational titles may receive a concise,
factual English alternative supported by a quoted passage from the article body or
source summary. Missing evidence cannot justify a replacement. Analysis distinguishes
substantive articles from utility pages such as About pages, RSS indexes and category
landing pages. Non-articles and unresolved page kinds block publication even when they
mention relevant technologies.

The publisher title remains in `articles.title`; the nullable `ai_title` is used by
public article responses, personalized feeds, bookmarks, search results and notification
titles. The web reader and Chrome/Edge extensions consume the same public title.
Slugs and canonical URLs do not change. Analysis job results retain the replacement,
page classification, supporting passage and rationale. Source-content edits clear the
generated title along with the existing analysis. A new analysis that keeps the source
title clears a previous replacement.

## Rollout and existing articles

Apply migration `0011` before starting the new application and workers. Existing rows
keep their current titles until explicitly reanalyzed. No migration performs AI calls
or changes editorial decisions. No existing production articles have been reanalyzed
as part of this implementation.

After deployment, queue selected articles individually using their database UUIDs:

```sh
docker compose exec api devfeed articles analyze ARTICLE_UUID
```

For Kubernetes, run the same `devfeed articles analyze ARTICLE_UUID` command inside
the deployed API container. This queues the normal durable analysis job; it does not
bypass configured AI capacity. Inspect its result using:

```sh
docker compose exec api devfeed articles analyses --article-id ARTICLE_UUID
```

Review the selected article's source content first. A successful reanalysis returns
it to pending/unpublished and then follows the existing source publication policy;
full-auto mode may publish it again if eligible. Utility pages remain blocked. The
existing `analysis-backfill` command targets pending, unpublished articles, so it is
not a backfill command for all published titles. Do not use a blanket reanalysis for
this cleanup. Existing search indexes should be rebuilt through the normal search
indexing workflow when rolling out the new display-title projection.
