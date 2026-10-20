# Shared HTTP contracts

## Reader destinations

The reader destination contract in [reader-destinations.json](src/reader-destinations.json)
defines route identities, semantic path parameters, approved query fields, navigation metadata,
and privacy-safe labels. The reader and browser extensions resolve navigation from its TypeScript
consumer; the user API validates login destinations with its Python consumer. The JSON is included
in the Python package and its build cache inputs.

Add or change destinations in that contract instead of maintaining an authentication allowlist.
Finite parameter values map semantic identities to URL segments, such as `tutorial` to `tutorials`.
Routes explicitly declare whether they can be login destinations and whether the embedded reader
owns them. The `/settings` alias resolves to the profile section. Unknown routes and query fields,
duplicate query keys, invalid parameter values, and unsafe URLs are rejected.

Public page definitions declare their sitemap frequency, priority, and publication source in the
same contract. Static pages serialize route identities and enumerate declared parameter values;
inventory collections retain a separate identity from their page URL. Shard paths resolve through
the reader route contract before their metadata is published. Legal page titles, descriptions,
paths, and update dates also drive the actual legal pages and navigation. Generic route
serialization is independent of whether a route is approved as a sign-in destination.

Sitemap inventory reads use the public API client with an explicit fresh-read policy and a
55-second deadline. The retained inventory remains authoritative; XML responses retain their
60-second index and 300-second page/shard caches, conditional ETags, and protocol validation.

The public login API keeps its relative `return_to` URL parameter. It resolves that URL to
`{routeId, parameters, query}` before starting authentication and stores this record in the bound
OIDC flow. The callback validates the record again before exchanging the provider code or creating
a session, then serializes one canonical relative URL. Invalid UI links fall back to the reader
home; invalid direct API inputs return 422. The extension's sign-in flow uses its declared
completion destination and preserves the current reader page.

Run `npm run test:reader-destinations` to compare both runtimes against every declared destination
and malformed URL or stored-record cases. The scoped commit checks run this suite for contract or
consumer changes, alongside Python and frontend checks. Browser regressions for actual sign-in
links run with `node --test --test-name-pattern="login destinations" apps/web/tests/browser/feed.mjs`
and the same name filter on `apps/extensions/tests/reader.browser.mjs`, once the production web
and extension packages have been built. Set `DEVFEED_EXTENSION_BROWSER=edge` for the Edge package.

## Browser OIDC logout

The reader, admin and partner **Sign out** action revokes the current DevFeed
browser session and then opens the configured provider's end-session endpoint.
It also ends the SSO session and can sign the user out of other applications
sharing that provider session. There is no separate SSO sign-out option.

Reader extensions reserve a normal browser tab during the click, navigate it to
the provider only after local revocation succeeds, and clear personal data in
all open new tabs. If the backend cannot revoke the session, the reserved tab
closes and the reader offers a retry. A provider outage before initiation leaves
the local session intact. Once local revocation succeeds, provider sign-out still
requires the browser to reach and complete the provider page; an interrupted
navigation cannot be reported as confirmed SSO termination.

### Zitadel client registration

Configure each dedicated OIDC application separately. The origin in this table
is that application's configured browser base URL, not an internal container
address. Use HTTPS origins reachable by Zitadel for back-channel notifications.

| Client  | Back-channel logout URI                                   | Registered post-logout redirect URI                 |
| ------- | --------------------------------------------------------- | --------------------------------------------------- |
| Reader  | `<reader-origin>/api/v1/user/auth/backchannel-logout`     | `<reader-origin>/api/v1/user/auth/sso/callback`     |
| Admin   | `<admin-origin>/api/v1/admin/auth/backchannel-logout`     | `<admin-origin>/api/v1/admin/auth/sso/callback`     |
| Partner | `<partner-origin>/api/v1/partner/auth/backchannel-logout` | `<partner-origin>/api/v1/partner/auth/sso/callback` |

Set `backchannel_logout_uri` on each Zitadel application and register the exact
post-logout URI. For each reader alias in `DEVFEED_USER_ALLOWED_ORIGINS`, also
register `<alias>/api/v1/user/auth/sso/callback`; completion returns to that alias
to preserve its host-only cookie binding. Allow subject-only notifications (`backchannel_logout_session_required=false`)
if desired; SID notifications are supported and preferred. Zitadel registers
notifications on new authorization requests, so sign in again after registration.
The existing issuer, client, organization and base URL settings are sufficient;
no additional secret, logout URL setting or client ID is needed. The advertised
`end_session_endpoint` must be on the configured issuer origin.

SSO initiation uses the browser-authenticated, origin/CSRF-protected
`POST /v1/<service>/auth/sso/logout` operation. Its response contains the validated
provider URL with client ID, registered post-logout URI and a random state value.
Completion requires a separate HttpOnly browser cookie plus a single-use Redis
flow and does not recreate any local session. The previous `/auth/logout` API
remains a local-revocation compatibility operation, but product sign-out controls
all use the coordinated SSO operation.

The back-channel operation accepts a bounded URL-encoded `logout_token` POST and
authenticates it independently of browser cookies, Origin or browser CSRF.
The gateway strips browser credentials from this operation. Only the configured
issuer discovery/JWKS supplies signing keys. Tokens require an allowed asymmetric
algorithm, exact issuer, intended client audience, fresh integer `iat` (at most
15 minutes old, at most 60 seconds ahead), `jti`, the back-channel event, no nonce,
and usable SID and/or subject. Expiration is enforced when supplied. Duplicate
parameters, JSON claim members, mismatched signatures and token-supplied key URLs
are rejected. Redis/provider outages return retryable 503 responses.

The `http://schemas.openid.net/event/backchannel-logout` event key is the exact
namespace mandated by OpenID Back-Channel Logout section 2.4, not an HTTP endpoint.
Its constant has a line-specific Sonar false-positive annotation for that reason.
Header parsing only selects an allowed key from the configured JWKS; signature
verification remains explicitly enabled before any logout claims are used.

References: [Zitadel back-channel logout](https://zitadel.com/docs/guides/integrate/back-channel-logout),
[Zitadel end-session endpoint](https://zitadel.com/docs/apis/openidoauth/endpoints#end_session_endpoint),
and [OpenID back-channel logout](https://openid.net/specs/openid-connect-backchannel-1_0.html).

### Correlation, races and existing credentials

Only validated issuer, client, subject, optional SID and ID-token issued-at time
are retained in private Redis browser-session context. None is added to public
identity responses. Raw ID/access/refresh tokens are not retained for logout.

Revocation uses hashed lookup keys separated by service, issuer and client. Each
session checks at most three keys; no keyspace scan or unbounded per-user session
list is required. A SID event matches that SID only. A SID+subject event matches
both; it never falls back to all sessions for the subject. A subject-only event
revokes sessions issued at or before its timestamp, including sessions without
SID. Newer authentications remain valid. Same-second subject authentications
fail closed and can retry with a newly issued ID token.

Atomic Redis scripts preserve monotonic subject cutoffs and make exact duplicate
deliveries idempotent. Conflicting reuse of a JTI is rejected. Event fingerprints
expire after 17 minutes. Revocation markers expire after the maximum supported
browser absolute lifetime (90 days), plus the callback window and clock skew.
Old session payloads expire normally; an acknowledged marker prevents their use,
including if a callback writes late or idle renewal races with logout. New
callbacks check the marker before returning a session; all browser authorization
paths and the renewal response check it too. Redis eviction or loss of revocation
records requires revoking all browser sessions before recovery; use the existing
`noeviction` Redis policy and durable storage.

This rollout requires existing reader/admin/partner browser sessions to sign in
again. Admin/partner browser policy versions change; the reader denies legacy
records without changing its policy key shared by independent MCP grants. Legacy sessions without
provider correlation are denied rather than guessed from email or user ID. A SID
event cannot revoke a different session which never had a SID.

Independently issued MCP access/refresh credentials are outside this browser OIDC
session contract. Neither product sign-out nor back-channel logout silently
revokes those credentials. Use MCP credential revocation separately.

### Verification and deployment gate

Signed-provider tests cover all three applications and run against a disposable
Redis database 15 when `DEVFEED_TEST_REDIS_URL` is set. They do not change client
registrations. Before deployment, register all URIs, establish fresh sessions in
the three applications, end the SSO session in Zitadel, observe real notification
delivery, and verify each old browser session is rejected. Also verify that a
session for another SID/client remains valid and record notification retry
behavior during a dependency outage. Local tests or a successful provider URL
redirect alone are not evidence that Zitadel delivered a notification.
