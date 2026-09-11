# Changelog

Application releases and Alembic schema revisions are separate identifiers.

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
  scanned and tested multi-platform runtime images, including isolated Codex.
- Authenticated TLS transport for remote AI workers and configurable database pool
  limits for shared PostgreSQL deployments.
- Updated JavaScript/Python dependencies, Node 24 LTS images and Codex 0.154.0.
  ESLint remains on its latest compatible 9.x release because the React plugin does
  not support ESLint 10; Pydantic controls its required `pydantic-core` version.
