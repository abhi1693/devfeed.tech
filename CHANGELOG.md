# Changelog

Application releases and Alembic schema revisions are separate identifiers.

## 0.0.4 — 2026-09-12

- Load job source and article names with the job page, removing per-row browser
  requests while retaining the existing SQL query budget.
- Release database connections before job-list and source-detail response
  validation, preventing connection-pool contention during concurrent reads.

No database schema changes.

## 0.0.3 — 2026-09-12

- Replace the source-output scatter plot with ranked publication bars, visible
  source names and exact counts. Show fetch failures separately.
- Format AI token summaries and chart labels with compact lowercase units such as
  `100k` and `61.4m`, retaining exact counts in tooltips.
- Explain publisher browser challenges during feed validation instead of showing
  an unexpected HTTP 202 error.

No database schema changes.

## 0.0.2 — 2026-09-12

- Pause automatic polling, notification streams and infinite scrolling when a
  browser tab is hidden or unfocused. Resume on return while preserving displayed
  data and the saved refresh interval, including Off.
- Show daily AI token usage by job type on the admin overview, including article
  analysis, topic analysis and research verification.
- Accept empty GitHub topic aliases, retain the first 50 aliases when imports
  exceed the limit, tolerate duplicate unused metadata and explain import failures.
- Normalize topic descriptions to plain text throughout the APIs, reader app and
  administration, including older stored descriptions.
- Refresh the product README with branding, website links and workflow guidance.
- Adopt centrally maintained CI workflows for ARM64 tests, security checks and
  verified publication of all six runtime images.

No database schema changes. Existing accounts, content, preferences and AI sign-in
state are preserved.

## 0.0.1 — 2026-09-12

First release of DevFeed, a developer news aggregation and discovery platform.

- Public reader app with article slugs, search, content filters, topic/source browsing,
  Markdown, article previews, keyboard navigation, themes and loading states.
- Independent OIDC user and admin sign-in with PKCE, organization checks, secure
  server sessions, CSRF protection and role-gated administration.
- User preferences, topic/source follows, likes, personalized recommendations and
  authenticated source suggestions with feed validation and editable feed names.
- Isolated administration app with operational overview, publishing and taxonomy
  workflows, source review, user analysis, job history and bounded runtime logs.
- Durable RQ ingestion and enrichment, original-article click tracking with shared
  abuse limits, public-response filtering, and Redis response caching.
- Full automation for AI analysis, topic research, classification and evidence-based
  source approval. Uncertain or irrelevant source suggestions remain unapproved.
- Separate notification environments and credentials for reader and admin inboxes.
- Deferred GA4 integration and typed events for original-article clicks, likes,
  follows, suggestions and saved preferences, without account identifiers or free text.
- One initial database migration (`0001`), operator CLI and six independently built,
  scanned and tested ARM64 runtime images, including isolated Codex.
- Authenticated TLS transport for remote AI workers and configurable database pool
  limits for shared PostgreSQL deployments.
- Updated JavaScript/Python dependencies, Node 24 LTS images and Codex 0.154.0.
  ESLint remains on its latest compatible 9.x release because the React plugin does
  not support ESLint 10; Pydantic controls its required `pydantic-core` version.
