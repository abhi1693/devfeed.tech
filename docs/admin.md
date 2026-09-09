# Administration services

Administration is separate from the future reader webapp. It supplies
organization-scoped sign-in, sign-out, an unchanged read-only overview, and
page-based content management. CLI workflows continue to work without either
web service running.

## Boundaries

| Service | Responsibility | Runtime dependencies |
| --- | --- | --- |
| `apps/admin` | Next.js App Router UI and `/api/v1/admin/*` gateway | Admin API only; no database, Redis or OIDC credentials |
| `apps/admin-api` | FastAPI OIDC, admin authorization, sessions, content CRUD, editorial workflows, run history | Existing PostgreSQL, existing Redis, configured OIDC provider |
| `apps/api` | Public discovery and pending source submissions | Existing PostgreSQL and Redis; no admin/OIDC imports |
| `apps/aggregator` | Existing background ingestion/enrichment pipeline | Unchanged |

The backend services depend on `devfeed_core`, never on one another's application
packages. Content data and business rules remain shared with the existing pipeline;
this is process/code/deployment isolation, **not database-per-service isolation**.
The existing migration owner remains the CLI. This change needs no migration.
Sessions and one-time login flows use only the `devfeed:admin:*` Redis namespace
with TTLs; public response-cache clearing never clears login sessions.

Deploy the admin API on a private network, reachable by the admin webapp, not through
the public API ingress. Deploy the admin webapp on its own origin, not the reader
site's origin. Each service has a separate Dockerfile/image and runtime environment.
The root backend image excludes the admin application and its OIDC dependencies.

## OIDC configuration

Create a **dedicated** OIDC application for DevFeed administration. Use authorization
code flow with PKCE S256 and query-mode callbacks. Endpoints and signing keys are
discovered from the issuer's `/.well-known/openid-configuration`; there are no
hardcoded authorization/token/JWKS URLs or Zitadel SDK dependencies.

Set these for the admin API (root `.env` during Python development):

```dotenv
DEVFEED_ADMIN_BASE_URL=https://admin.example.com
DEVFEED_OIDC_ISSUER_URL=https://identity.example.com
DEVFEED_OIDC_CLIENT_ID=your-devfeed-admin-client-id
DEVFEED_OIDC_ORGANIZATION_ID=your-organization-id
DEVFEED_ADMIN_REQUIRED_ROLE=superuser
DEVFEED_OIDC_TOKEN_ENDPOINT_AUTH_METHOD=none
DEVFEED_ADMIN_COOKIE_SECURE=true
```

The issuer must exactly match discovery/token `iss`, including any trailing slash.
Register this exact redirect URI with the provider:

```text
https://admin.example.com/api/v1/admin/auth/callback
```

`none` is for a registered PKCE client without a secret. For a confidential client,
use `client_secret_basic` or `client_secret_post` and supply
`DEVFEED_OIDC_CLIENT_SECRET` only to the admin API. PKCE is mandatory in every mode.
No provider registration, administrator, credential, or organization is created
automatically, and values from Wardn AI are not reused.

Zitadel's organization scope enforces membership and asserts the resource-owner
claims ([Zitadel scope reference](https://zitadel.com/docs/apis/openidoauth/scopes)).
The service defaults to:

```dotenv
DEVFEED_OIDC_ORGANIZATION_SCOPE_TEMPLATE=urn:zitadel:iam:org:id:{organization_id}
DEVFEED_OIDC_ORGANIZATION_CLAIM=urn:zitadel:iam:user:resourceowner:id
```

Other OIDC providers can configure their own organization scope template and exact
claim name. Organization semantics are provider-specific, not an OIDC standard.
The verified ID token or subject-matched UserInfo must assert the configured org.
An organization member is **not automatically an administrator**: the verified
identity must also have the exact, case-sensitive `superuser` role for that
organization. There is no local user-ID allowlist, email/domain-based access, or
wildcard admin access.

### Administrator roles

In Zitadel, create the role key `superuser` in the project containing the DevFeed
admin OIDC application, then assign that project role to each administrator in the
configured organization. This is an application role, not a Zitadel console role
such as `ORG_OWNER`. DevFeed requests the role using these defaults:

```dotenv
DEVFEED_ADMIN_REQUIRED_ROLE=superuser
DEVFEED_OIDC_ROLES_CLAIM=urn:zitadel:iam:org:project:roles
DEVFEED_OIDC_ROLES_FORMAT=organization_map
DEVFEED_OIDC_ROLE_SCOPE_TEMPLATE=urn:zitadel:iam:org:project:role:{role}
```

The roles claim must reach the verified ID token or subject-matched UserInfo.
Zitadel supports requesting it through the role scope above; its project setting
**Assert Roles on Authentication** enables UserInfo role assertions, and the
application setting **User Roles Inside ID Token** enables ID-token role assertions
([Zitadel role configuration](https://zitadel.com/docs/guides/integrate/retrieve-user-roles)).
With the default format, DevFeed expects an object like:

```json
{
  "urn:zitadel:iam:org:project:roles": {
    "superuser": {"your-organization-id": "your-organization-domain"}
  }
}
```

A grant in another organization does not count. Missing or malformed role claims
deny access. If both ID token and UserInfo assert roles, the required role must
appear in both; conflicting assertions never expand privileges. DevFeed checks
only the exact configured claim, never similarly named claims for other projects.
The provider's project-specific role claim can also be selected explicitly with
`DEVFEED_OIDC_ROLES_CLAIM` when available for the registered application.

For a generic provider exposing an application-scoped string array, configure its
exact claim name, set `DEVFEED_OIDC_ROLES_FORMAT=string_list`, and set
`DEVFEED_OIDC_ROLE_SCOPE_TEMPLATE` to its required scope (or leave it empty if no
extra scope is needed). For example, a claim named `roles` can contain
`["superuser"]`. The separate organization check is still mandatory.

Missing required OIDC settings disable login and deny protected endpoints.
Issuer, client, organization, role-policy, or session-policy changes invalidate
existing sessions and outstanding login flows after the admin service reloads
its settings. `DEVFEED_ADMIN_ALLOWED_SUBJECTS` is no longer used and should be
removed from deployments. Existing ID-allowlist sessions require a fresh sign-in.

## Run locally (manually)

The existing `DEVFEED_DATABASE_URL` and `DEVFEED_REDIS_URL` remain the only database
and Redis connection variables; do not add competing host/password settings.

For HTTP-only development, set the admin API's base URL to your actual frontend
origin (for example `http://localhost:3000`) and explicitly set
`DEVFEED_ADMIN_COOKIE_SECURE=false`. Register the corresponding HTTP callback with
the OIDC application using its development configuration. Production requires
HTTPS/secure cookies. The issuer itself must use HTTPS except for loopback tests.

Copy `apps/admin/.env.example` to `apps/admin/.env.local` and fill:

```dotenv
DEVFEED_ADMIN_API_URL=http://127.0.0.1:8001
DEVFEED_ADMIN_BASE_URL=http://localhost:3000
```

These are example local addresses, not defaults. The base URL must match the admin
API's setting exactly. For a LAN browser, use the LAN frontend origin consistently
and set `DEVFEED_ADMIN_DEV_ORIGINS=192.168.1.101` in `apps/admin/.env.local`
(substitute your LAN hostname). Multiple hosts are comma-separated, without schemes
or ports. Restart the Next.js dev server after changing this value: it feeds
[`allowedDevOrigins`](https://nextjs.org/docs/app/api-reference/config/next-config-js/allowedDevOrigins)
to permit development resources such as the HMR connection. This is separate from
OIDC redirects and API CSRF origin checks. None of these variables are `NEXT_PUBLIC_*`.

Install and then run in separate terminals when ready:

```sh
uv sync --all-packages --locked
npm ci
uv run uvicorn devfeed_admin_api.main:app --port 8001 --reload --no-proxy-headers --no-access-log
npm run admin:dev
```

The public API/worker/scheduler commands are unchanged. Admin login/overview does
not require the public API process to be running. Do not run migrations just to
enable administration. No command in setup automatically provisions dependencies.

The Next.js gateway allows 120 seconds for source creation and 210 seconds for
source preview (RSS validation plus an optional website fetch). Other requests
keep a 45-second deadline. Any additional reverse proxy must allow at least these
source-operation deadlines. Article metadata edits return HTTP 409 while page
enrichment is queued or running; wait for the job to finish and reload before
saving, so a late enrichment result cannot overwrite a successful manual edit.

## Security and API contract

- Backend-owned PKCE verifier, nonce and browser-bound state; state is consumed
  once with Redis `GETDEL` (Redis 6.2+). No verifier or provider token goes to JS.
- ID token signature, algorithm allowlist, exact issuer, audience, authorized party,
  subject, expiry, issued-at and nonce are verified. UserInfo cannot switch subjects.
- Random opaque host-only, HttpOnly, SameSite=Lax session cookies. Secure deployments
  use `__Host-` cookie names. Sessions expire at the earlier of the ID token expiry
  and `DEVFEED_ADMIN_SESSION_TTL_SECONDS` (default eight hours). No silent refresh.
- Every protected read checks the session and its verified required role; content
  mutations additionally require the exact configured `Origin` and `X-CSRF-Token`
  returned by `/auth/me`.
- Roles are verified at sign-in and stored for that session, not re-fetched from
  the provider on every request. Provider role changes take effect at the next
  sign-in or session expiry; removing a provider role does not immediately revoke
  an already-issued DevFeed session. Sign out and back in after changing grants.
- Failed sign-in recovery is explicit: the error page's **Sign in again** button
  and the signed-out page request `/auth/login?reauthenticate=true`. This starts a
  new PKCE flow with OIDC `prompt=login` and `max_age=0`, so a provider SSO session
  cannot silently complete the retry. The signed ID token must include a fresh
  `auth_time` relative to the server-stored flow start (60 seconds clock tolerance).
  Required-role and organization checks still apply; reauthentication never grants
  missing permissions. This uses [standard OIDC authentication parameters](https://openid.net/specs/openid-connect-core-1_0.html#AuthRequest),
  also supported by [Zitadel](https://zitadel.com/docs/apis/openidoauth/endpoints#additional-parameters).
- Once a callback's state is browser-bound and consumed, its previous local session
  is revoked before exchanging the new code. Authorization denial, invalid identity,
  or provider cancellation clears the local session and state cookies. Invalid,
  unbound, or replayed callbacks cannot revoke a current session. No failed identity
  is stored as an admin session. Dependency failures abort login rather than minting
  a replacement session. DevFeed does not end provider-wide sessions or sign the
  user out of other applications; the retry requests fresh authentication instead.
- Admin responses are `no-store`, outside the public route cache. The gateway
  forwards only the admin namespace, never follows redirects, and preserves each
  Set-Cookie header. There is no credentialed CORS or browser bearer-token storage.
- Sign-out is an explicit `POST /v1/admin/auth/logout`, available from the admin
  header. It revokes this browser's Redis session, clears session and pending-login
  cookies, and navigates to `/login?signed_out=1` with a confirmation. Replaying the
  old session cannot access admin endpoints. It does not sign out other DevFeed
  sessions or other applications at the identity provider.
- Sign-out remains available after expiry or a role/policy change. The exact
  configured origin is always required; an existing session also requires its
  CSRF token. If the session is already gone, repeated sign-out succeeds and clears
  stale cookies. Redis failures return an error without claiming successful
  revocation, so the user can retry. Server-side revocation and browser cookie
  expiration follow the [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#manual-session-expiration).
- Application logs omit codes/tokens/cookies/query strings. Next request logging is
  disabled; run Uvicorn with `--no-access-log`. Configure ingress access logs to omit
  callback query strings too. Rate-limit login/callback at the private/admin ingress.

The admin API exposes `/v1/admin/auth/{config,login,callback,me,logout}` and
`GET /v1/admin/overview`. Existing taxonomy operations moved to
`/v1/admin/topics` and `/v1/admin/tags`; ingestion reads moved to
`/v1/admin/ingestion/*`. Old public writes now return 405 and old ingestion routes
return 404. Public taxonomy reads and source submissions are unchanged.

## Object pages and navigation

The sidebar groups **Content** (articles, sources), **Taxonomy** (topics,
tags, topic relationships), and **Operations** (feed ingestion,
article/image/source enrichment, AI analysis). Overview's content is unchanged.
On small screens the sidebar becomes a toggled navigation panel.

Admin browser URLs follow the navigation hierarchy. The versioned API and auth
endpoints keep their existing contracts.

| Area | List URL |
| --- | --- |
| Articles | `/content/articles` |
| Sources | `/content/sources` |
| Topics | `/taxonomy/topics` |
| Tags | `/taxonomy/tags` |
| Topic relationships | `/taxonomy/relationships` |
| Feed ingestion | `/jobs/ingestion` |
| Article enrichment | `/jobs/enrichment/articles` |
| Image enrichment | `/jobs/enrichment/images` |
| Source enrichment | `/jobs/enrichment/sources` |
| AI analysis, combined | `/jobs/analysis` |
| Article / topic AI analysis | `/jobs/analysis/articles`, `/jobs/analysis/topics` |
| Notification delivery | `/jobs/notifications` |

`/content`, `/taxonomy`, `/jobs`, and `/jobs/enrichment` have index pages.
Content resources use `/new`, `/{id}`, `/{id}/edit`, and `/{id}/delete` for
creation, details, editing, and explicit deletion confirmation. Available object
sections have their own paths: `/{id}/related`, `/history`, `/evidence`, or `/logs`.
Review, classification, and fetch workflows remain nested under their object.

Topic import and proposal review live at `/taxonomy/topics/import` and
`/taxonomy/topics/proposals`, with individual reviews at `/proposals/{id}` under
the topics path. Keyword enrichment is `/taxonomy/topics/{id}/enrich`.
AI relationship discovery uses `/taxonomy/relationships/discover`; its review table
and individual reviews are `/taxonomy/relationships/proposals` and
`/taxonomy/relationships/proposals/{id}`. Research and approval require active topics.
Analysis run details use `/jobs/analysis/articles/{id}` or
`/jobs/analysis/topics/{id}`. Relationship details use
`/taxonomy/relationships/{from-topic-id}/{relationship}/{to-topic-id}`;
the database identity remains unchanged.

Old flat resource URLs, composite job/relationship links, and `?tab=logs` links
permanently redirect to the new paths, retaining search, filters, sorting,
pagination, and batch IDs. New inbox notifications use the canonical paths;
existing notifications still work. The shared browser route map is
`apps/admin/src/lib/routes.ts`; API resource keys remain internal adapter details.

Object pages have details, related-object, and (where available) history/evidence
sections, linked breadcrumbs, and standardized View/Edit/Delete actions. This
follows the separation of list, object, edit, and delete views in
[NetBox's object templates](https://netbox.readthedocs.io/en/stable/plugins/development/templates/).
Forms keep failed values and display server validation errors. Relationship
selectors search and paginate the API; they do not load a static catalog.

All five pipeline run types include a live runtime log panel and a Logs tab.
The shared viewer supports attempt context, level/search filters, safe stack
details and plaintext download. Completed runs remain readable within configured
Redis retention; previous console-only runs cannot be backfilled. See
[runtime logging](logging.md#runtime-logs-on-job-pages) for limits, storage
failure behavior and worker restart requirements. Overview remains unchanged.
Topic facts are repeatable structured fields with source URLs and retrieval dates.
AI prose remains visibly separate and is not writable through original-metadata forms.

TanStack React Table v9 supplies the shared tables. Search, filters, sorting, and
pagination are performed by SQL, with stable ID tie-breakers; list state is in the
URL. Admin list responses use `{items, total, limit, offset}` (including topics
and tags), with a maximum page size of 100. Public API contracts are unchanged.
OpenAPI and Orval are generated together; no screen constructs its own HTTP routes.

### Workflows and deletion rules

- The source form is grouped into feed setup, profile, and polling settings. New
  source details load automatically after a 650 ms pause on a valid-looking HTTP(S)
  URL. The backend validates real RSS/Atom before returning feed metadata, then
  supplements missing branding from the feed's declared website using the guarded
  fetcher. Website failures retain feed metadata and show a warning. There is no
  fetch button or success banner; errors remain visible beside the URL.
- `POST /v1/admin/sources/preview` requires admin authorization and CSRF. A short
  read-only duplicate check finishes before network I/O; no source, review, job,
  validator, or article is saved. Source creation always revalidates the feed.
  Existing feed URLs produce a field-level HTTP 409 during preview and creation;
  the unique constraint covers concurrent submissions too. Source edit pages keep
  identity fields immutable and do not perform a new-source lookup.
- URL changes cancel pending lookups and discard stale responses. Automatically
  filled metadata is cleared for a different URL; manual edits (including clearing
  a field) are preserved. Source, article, and classification language fields use
  a shared dropdown with readable language names, regional variants, and an unknown
  option where permitted. Existing regional/three-letter codes are retained.

- `articles/{id}/classify`: assign topic roles/relevance and topic/tag labels
  using verbatim evidence. An active primary topic and developer relevance remain
  publication requirements. Manual classification and metadata edits unpublish
  the article and reset approval; edits also invalidate generated prose. Revision
  checks reject stale forms instead of overwriting newer editorial changes.
- `articles/{id}/review`: approve, reject, publish, or unpublish; rejection needs
  a reason. Approval does not itself publish. The audit actor is always the
  authenticated subject, never a caller-supplied field.
- `sources/{id}/review`: approve or reject, with attributed history. Private admin
  creation validates the feed and records an approval, while public submissions
  remain pending. Creating a disabled source does not queue work.
- `sources/{id}/fetch`: request/coalesce an ingestion job and open its run page.
  Dispatch remains owned by the scheduler; the admin service never imports the
  aggregator application or starts a worker. Feed URL/type and article canonical
  URL/source identity are immutable in edit forms to preserve ingestion identity.
- Deletion requires typing `DELETE`. Categories with children, tags, or article
  assignments and topics/tags with references are protected. Articles must be
  unpublished and have no active jobs. Sources cannot have linked articles or
  active jobs. Deleting an article removes its evidence, relationships, completed
  jobs, and reviews; deleting a source removes completed runs/reviews. Reject or
  disable an object when history should be retained. A deleted article can be
  rediscovered from RSS; rejection preserves the decision instead.
- Worker runs and review history are immutable diagnostic records, not general
  CRUD targets. Their dedicated run pages show status, errors, timing, results,
  and links to the owning source/article. Private worker lease tokens are omitted.

All new endpoints stay behind the admin session and CSRF boundary and outside
public caches. Existing core transaction hooks invalidate affected public caches
after committed content changes.

## Frontend conventions and checks

`src/components/atoms` contains shadcn primitives, configured through
`components.json` → `aliases.ui`. Molecules compose primitives, organisms implement
screen sections, templates own layout, and `src/app` pages compose screens. Keep
API/session logic out of atoms. No parallel `components/ui` directory.

`Button` in `components/atoms/button.tsx` is the only app-owned button component,
including form actions, table sorting, toolbar controls, help and preview icons.
Use its variants (`default`, `outline`, `secondary`, `ghost`, `destructive`,
`destructive-ghost`, `link`) and size options instead of repeating interaction
styles. Buttons share a pointer cursor, subtle hover/pressed colors, keyboard
focus rings and reduced-motion support. Disabled controls retain a not-allowed
cursor and do not receive hover/pressed feedback; disabled fieldsets are honored.

The default type is `button`; forms must explicitly use `type="submit"`.
Use `loading` and optional `loadingText` for async actions: the component provides
the spinner, busy state and disabled activation. Keep business logic, mutation
guards, requests and toasts in the caller. `asChild` preserves links and Radix
trigger composition; loading slotted controls retain their child content. Give
icon-only buttons an `aria-label`.

`RecordActions` owns the icons and ordering for object action groups. Detail
pages show resource-specific actions first, then Edit, with Delete always last.
Table rows use compact icon-only Edit and Delete controls with accessible names
and hover/focus tooltips; the record name already links to its detail page, so
there is no separate View action. Detail-page actions retain visible labels.
Decorative icons are hidden from assistive technology, and keyboard order follows
the visual order. Read-only worker tables omit the empty Actions column and keep
their linked run identifiers.

```tsx
<Button type="submit" loading={saving} loadingText="Saving…">Save source</Button>
<Button variant="outline" onClick={cancel}>Cancel</Button>
```

`Field` in `components/molecules/field.tsx` is the reusable field shell. It composes
the `FieldLabel`, `FieldDescription`, `FieldError` and Tooltip atoms, and owns
control IDs, required indicators, disabled state, subtext and accessible error
associations. Tooltips support hover, keyboard focus, Escape and tap; important
instructions and validation errors stay visible outside tooltips.

Compose controls rather than duplicating labels and error markup. Spread the
render function's props onto the control (or a component that forwards them):

```tsx
<Field name="website_url" label="Website" required
  subtext="The publisher’s public website."
  tooltip="This is separate from the RSS feed URL." error={errors.website_url}>
  {control => <UrlInput {...control} value={website} onChange={event => setWebsite(event.target.value)} />}
</Field>
```

IDs are generated when omitted; supply an explicit unique `id` when needed.
Only existing help/error elements are referenced by `aria-describedby`.
`FormField` is the resource-schema adapter over this shell: `FieldSpec.help`
supplies subtext, and `FieldSpec.tooltip` supplies optional extra context. Native
inputs, textareas, checkboxes, URL previews and custom comboboxes share the shell.
Use `FormField` for standard controls, including number fields with `min`, `max`
and `step`. Custom editors such as file uploads, import data and delete
confirmation compose `Field` directly with the existing input/textarea atoms.
Checkboxes use `Input type="checkbox"`, including table selection.

Reuse field definitions from `lib/resources.ts` when editing the same data in
another workflow. Topic proposal review uses the topic definitions with lifecycle
status excluded; approval and rejection remain explicit decisions. Both topic
forms use `FactsEditor` for adding, editing and removing sourced facts, with
`initialFacts`/`factsPayload` preserving untouched retrieval timestamps. Reviewed
proposals display these same controls disabled. Do not add parallel topic fields,
facts editors, URL previews or select implementations to individual forms.

Use the shared `Select` molecule for short, fixed choices (status, format, page
size, refresh interval). `Combobox` extends `Select` with search; language and
entity pickers extend `Combobox` with domain data. Both variants share the same
trigger, popover, option layout, selection and form validation. Radix Popover and
cmdk Command primitives live in `atoms`. Do not add visible native selects or
separate dropdown implementations in pages or forms. Triggers match input height,
borders and corners; menus support pointer, touch, arrows, Enter and Escape. Plain
selects support typeahead without displaying a search field.
Language choices are searchable by readable name or code. Entity searches remain
server-side, debounced and paginated; opening the control loads options, not every
page render. The current label is retained independently of search results.
Keep the non-interactive native-select bridge: it preserves required-field
validation, disabled fieldsets and FormData for custom triggers.

Refreshable lists, details, proposals, evidence previews and job logs use
`RefreshInterval`: Off, 5s, 10s (default), 15s, 30s, or 1 minute. The selected interval
is shared by the admin session and retained during client navigation. `usePolling`
and `useRequest` preserve displayed records, cursor position, table selections and
unsubmitted filters, skip hidden tabs, prevent overlapping automatic requests, and
cancel automatic reads when disabled or unmounted. Initial loads and explicit
retries still work with Off. Enrichment previews pause while keywords are selected
or submitting so an automatic update cannot replace the reviewed preview token.
Form values are not polled. Job logs retain their cursor when the interval changes
and stop fetching after final settling reads. Reuse these controls and hooks;
do not add page-specific refresh buttons or hard-coded polling intervals.

Use the `UrlInput` atom for URLs. `LogoUrlField` and `ImageUrlField` molecules
extend it for source branding, topic logos and article images, preserving native
URL validation and the same input height. Logos have a small inline preview;
large images load only when hovered or opened with the preview button (keyboard
and touch supported). Logo changes are debounced, stale previews are cleared,
and failed images show a quiet fallback without changing or rejecting the value.
Previews load HTTP(S) images directly in the browser with no referrer, not through
a server-side image proxy. Existing source auto-fill and API validation remain
unchanged.

Detail pages use `ImagePreviewLink` for logo/image fields instead of displaying
their raw URLs. Logos remain small, cover images use bounded thumbnails, and
clicking or keyboard-activating either opens the original URL in a new tab with
`noopener noreferrer`. Failed previews keep that link available; missing values
remain placeholders. Other URL fields remain normal text links.

The root layout mounts one Sonner `Toaster` atom. Use `notify` from
`src/lib/notifications.ts` for success, error, warning and info feedback, and
`notifyFailure` for API failures (safe error text and session-expiry redirects).
Confirm mutations only after the API succeeds, before client-side navigation.
Keep field validation, destructive-action warnings, and persistent unavailable
states inline; do not replace them with disappearing notifications. Automatic
source previews and log polling do not emit success toasts. Background read
errors are deduplicated, and runtime-log failures notify once per outage.
Toasts are dismissible, respect reduced motion, and use a screen-reader live
region. Errors/warnings remain visible longer than routine confirmations.

Orval generates the client and types from the admin API's exported OpenAPI contract:

```sh
npm run admin:generate
npm run admin:lint
npm run admin:test
npm run admin:build
uv run pytest -q
```

Generation imports the app but does not start it or contact any service; required
core connection variables must still be supplied. Commit generated contracts with
future API changes. CI checks generation drift. `scripts/version.py` now keeps
Python, npm workspace and both lockfile release versions aligned at `0.0.1`.

From the repository root, each image can be built independently:

```sh
docker build -f apps/admin-api/Dockerfile -t devfeed-admin-api:local .
docker build -f apps/admin/Dockerfile -t devfeed-admin:local .
```

Keep runtime environment files/secrets outside images. No Docker Compose is needed.
# Notification inbox

Chimely supplies a persistent inbox alongside the existing toasts. The header
bell is hidden until notifications are configured. Background-job messages link
to their runs and runtime logs; notification deliveries have their own read-only
Operations pages. See [notification infrastructure setup](notifications.md).
