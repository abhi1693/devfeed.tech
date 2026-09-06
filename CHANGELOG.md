# Changelog

Record changes here before creating an annotated `vMAJOR.MINOR.PATCH` release tag.
Application releases and Alembic schema revisions are separate identifiers.

## Unreleased

- Shared searchable admin comboboxes for forms, languages, filters and page sizes,
  with click-only opening, input-matched styling and paginated API-backed choices.
- Shared admin success, error, warning and info toasts, preserving inline field
  validation and suppressing repeated background polling failures.
- Replaced the operator CLI's argparse parser with typed Typer command groups,
  contextual help and shell completion, preserving commands, JSON and exit codes.
- Bounded, sanitized Redis runtime logs for every pipeline job, with live admin
  log views, attempt context, search, level filters and plaintext downloads.
- Automatic, read-only source metadata lookup; grouped source forms, language
  dropdowns, and field-level duplicate-feed errors before fetching or saving.
- Recoverable admin role-denial login: clear the previous local identity on failed
  browser-validated callbacks and request verified fresh OIDC authentication on retry.
- Grouped admin sidebar, page-based article/source/taxonomy CRUD, related-object
  views, structured forms, and server-paginated TanStack tables; Overview is unchanged.
- Authenticated editorial/classification workflows, source review and ingestion
  requests, protected deletion, and read-only pipeline run/history pages.
- Reliable admin sign-out after session expiry or role-policy changes, server-side
  session revocation, pending-login cookie cleanup, and a signed-out confirmation.
- Isolated FastAPI admin API and Next.js administration app with separate Dockerfiles.
- Generic discovery-based OIDC with PKCE S256 and organization-scoped superuser authorization.
- Redis-backed admin sessions, CSRF protection and uncached administration routes.
- Simple atomic-design shadcn admin UI and Orval-generated API client.
- Taxonomy writes and ingestion diagnostics removed from the public API.

## 0.0.1 — initial baseline (not released)

- FastAPI discovery API and database-managed sources, nested categories and tags.
- RQ ingestion, scheduling, retries, article/image/source enrichment and language detection.
- Shared Redis GET caching and development-friendly structured/plaintext logging.
- Topic identities and relationships, AI analysis provenance and editorial publication controls.
- Operator CLI for ingestion, review, classification, enrichment and queue operations.
- One initial database migration; subsequent schema changes use incremental revision files.
