# Developer discovery scope

The table below records the initial foundation decisions from the daily.dev
review on 2026-09-06. It is a visual and product reference, not a specification
to duplicate feature for feature.

The application now includes editorial administration and the anonymous user
in `apps/web`, with optional user accounts and followed-topic feeds. See [development](development.md) for current functionality.
Browser extensions remain planned; the deferrals below describe the original phase.

| Observed daily.dev behavior | DevFeed decision for this phase |
| --- | --- |
| Technology tags shape topic preferences | Database-managed tags and aliases, with canonical topics as the subject catalog |
| Content types are separate from followed topics | Separate `content_type` field for article, tutorial, news, release, comparison and opinion |
| Sources and tags can be blocked | Anonymous include/exclude query filters; future clients can store preferences locally |
| Source curation and moderation affect the discovery feed | Explicit source configuration and polling enable/disable; moderation is deferred |
| Multiple feeds have different ranking signals | Ship a newest-first feed with search; introduce recommendation/popularity ranking when its inputs exist |
| Web and browser clients consume the same content experience | Shared, versioned public API and reserved monorepo application directories |

The distinction between tags and content type comes from daily.dev's
[advanced filtering documentation](https://docs.daily.dev/advanced-filtering-options/).
Its [personal feed guide](https://docs.daily.dev/filtering-content-feed/) explains
topic selection and saved preferences. DevFeed supports anonymous request filters plus optional followed-topic
preferences saved across devices; see [user accounts](user-accounts.md).

daily.dev documents recency, unique reads, upvotes and other community activity as
inputs to its popular feed. DevFeed has none of those engagement inputs yet, so its
chronological feed is labeled and implemented accordingly.
See [daily.dev feeds](https://docs.daily.dev/feeds/).

daily.dev also describes source vetting and automated/human moderation in its
[moderation documentation](https://docs.daily.dev/content-moderation/), and an
editorial source submission process in its
[source guide](https://docs.daily.dev/suggest-new-source/). Our initial quality gate
is manual source selection; keyword classification alone is not
a spam, quality or developer-relevance detector.

Deferred features include all accounts, authentication, administration, moderation,
bookmarks, reading history, social
voting, comments, Squads/communities, paid plans, AI summaries, a full article reader,
engagement analytics and behavioral recommendations. No differentiation or parity
claim is implied by implementing an aggregated feed and technology filters.
