# Source polling schedules

Admin source PATCH and CLI `devfeed sources update SOURCE_UUID --poll-interval SECONDS`
share the same locked scheduling service. A changed interval on a healthy, enabled,
approved source recalculates `next_fetch_at` from `last_success_at + new interval`;
overdue sources become due now for the next scheduler cycle. Unchanged intervals
and unrelated edits leave the pending schedule intact.

If no fetch has succeeded yet, an interval edit retains the existing initial due
time. Disabled/unapproved sources, queued or running ingestion jobs, and source
failure cooldowns retain their due times as well. Active retries keep their job
availability and upstream `Retry-After` delay. A successful worker completion uses
the latest interval for the next fetch. Re-enabling a healthy approved source
without changing its interval requests an immediate fetch; it does not bypass an
active job or failure cooldown. A combined interval/enable edit follows the cadence
rules above. Merely sending `enabled=true` for an already-enabled source does not
reschedule it. Adaptive cadence should reuse `reconcile_source_schedule`.

## Verified reader acquisition and activation

`devfeed readers acquisition-report --start 2026-10-01 --end 2026-10-17`
exports JSON for verified reader signups in the inclusive-start, exclusive-end UTC
signup window. The maximum window is 93 days, with at most 2,000 aggregate groups
and a 15-second read-only database query timeout. Help works without services.

Each group contains the recorded acquisition UTC date, source, attribution state,
verified signups, activated accounts, pending seven-day windows, matured accounts
without activation, and diagnostic delivery counts. Acquisition can precede the
selected signup window; a missing acquisition date remains null. The capture date
is a bounded browser first-observation timestamp linked through sign-in, not an
independently verified visit time. The output contains no account or GA identities.

- **Completed signup:** committed creation of a new account through the verified
  reader OIDC callback. The unique account identity arbitrates concurrent callbacks.
  Returning sign-ins, callback replay, failed authentication, and identity creation
  for other services do not create signup outcomes. Historical accounts are not
  backfilled as new signups.
- **Activation v1:** the first retained authenticated original-article open in
  `[signup_at, signup_at + 7 days)`. The exact seventh-day boundary is excluded.
  Previews, pageviews, sign-in and preference saves do not count. An outbound click
  does not prove reading completion, and activation does not measure retention.
- **Acquisition:** the first coarse landing in the browser's stored context, up to
  30 days old. Reader navigation and the identity provider cannot overwrite it.
  Known campaign sources/media are allowlisted; campaign labels must also appear
  in deployment configuration. Raw referrers, arbitrary query values and private
  paths are discarded. Direct, unknown and unavailable are separate states.
- **Measurement:** the existing GA client/session must be available and permitted.
  No identity is invented for blocked tags or storage. Activation requires fresh
  permission and a client bound to the current authenticated reader session.
  Switching accounts cannot reuse another account's session binding. Opting in
  later does not resend an earlier suppressed milestone.

First-party totals use the callback's durable account marker and retained reading
events, including when optional attribution persistence fails. Missing signup or
activation rows are reported as gaps. They do not silently become zero accounts.
The report has no complete visitor denominator and must not be labeled a visitor
conversion rate. An unactivated account with an open seven-day window is pending,
not a completed-window failure.

GA counts reconcile the two first-party milestones with `unavailable`, `pending`,
`attempted`, `http_accepted`, `failed` and `unknown` delivery rows. Missing rows are
included in unavailable coverage and also counted explicitly. Eligible means
eligible delivery rows, not every measurable visitor. Delivery states are current
at query time; the observation cutoff applies to first-party outcome timestamps.

Each diagnostic can make one committed send attempt. `http_accepted` means HTTP
acknowledgment with ingestion unverified. A timeout, crash or failure after claiming
an attempt is never retried automatically; GA4 does not promise deduplication of
these nonpurchase events. Measurement failures do not block authentication, reading
events or the existing X conversion path. Missing configuration and opted-out
milestones are not backfilled when tracking is later enabled.

### Deployment and GA4 verification

Apply migration `0025` before running the new application version. It adds a
nullable verified-reader signup timestamp and two narrow cohort/delivery tables.
Existing accounts retain a null marker. Account deletion cascades to these rows
and the existing reading ledger. Downgrading removes the measurement history.

Measurement defaults to disabled. Configure these values consistently:

| Setting | Consumers | Purpose |
| --- | --- | --- |
| `DEVFEED_READER_MEASUREMENT_ENABLED=true` | web, user-api | Enable the bridge. |
| `DEVFEED_ANALYTICS_ENABLED=true` | web, user-api | Permit web diagnostics. |
| `DEVFEED_EXTENSION_ANALYTICS_ENABLED=true` | web, user-api | Permit extension diagnostics. |
| `GOOGLE_ANALYTICS_ID` | web, user-api | Same GA4 web stream as existing analytics. |
| `DEVFEED_READER_GA_API_SECRET` | user-api only | Server-only Measurement Protocol secret. |
| `DEVFEED_READER_ACQUISITION_CAMPAIGNS` | web, user-api | JSON list of public campaign labels, default `[]`. |

The existing extension relay retains its separate server-only secret. Never expose
either secret to browser bundles or include them in request/error logs. The new
diagnostics send no email, account ID, OIDC subject/token, raw referrer or raw URL.
Account linkage is first-party; cross-device GA funnels cannot be inferred because
account identifiers are deliberately absent from GA payloads.

After an authorized rollout:

1. In GA4 property administration, mark the exact `sign_up` and `reader_activation`
   events as key events, counting once per event. Keep signup-intent events such as
   `dev_card_signup_started` separate. This configuration applies prospectively.
2. Create event-scoped custom dimensions for `acquisition_state`,
   `acquisition_source`, `acquisition_medium`, `acquisition_campaign`, `landing_route`,
   `client_platform` and `measurement_version`. Do not register account, client or
   session identities as custom dimensions. Allow 24–48 hours for new definitions.
3. Validate the bounded payload shape with Google's `/debug/mp/collect` endpoint
   using an existing permitted client/session and the server-held secret. An empty
   `validationMessages` array establishes structural validation only: this endpoint
   does not ingest events or validate the API secret.
4. Run a controlled new-account journey with a permitted campaign in web, Chrome
   and Edge. Verify one committed signup and one first original-open activation;
   repeat callbacks/opens and a returning sign-in without adding milestones. Repeat
   with analytics disabled, DNT/GPC, blocked storage and a switched account. The
   first-party outcomes remain measurable while ineligible GA diagnostics stay absent.
5. Record the UTC observation time and first-party export. Confirm event names and
   allowed parameters in GA4 Realtime, then in processed reports after their normal
   delay. Reconcile received GA event counts against eligible and HTTP-acknowledged
   attempts, retaining unknown attribution and delivery gaps. A production 2xx, an
   empty DebugView, or a zero report alone does not prove ingestion or no signups.
   Diagnostics do not manufacture engagement duration; Realtime session metrics
   can be incomplete, so also inspect processed event counts.

This checklist is rollout work; local fixtures and mocked provider responses do
not establish production GA reception or property configuration.

References: [recommended sign_up](https://developers.google.com/analytics/devguides/collection/ga4/reference/events#sign_up),
[Measurement Protocol transport](https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference),
[event validation](https://developers.google.com/analytics/devguides/collection/protocol/ga4/validating-events),
[key events](https://support.google.com/analytics/answer/13128484),
[custom dimensions](https://support.google.com/analytics/answer/14240153).
