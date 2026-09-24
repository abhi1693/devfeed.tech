# Public user

Anonymous Next.js website on port 3000. Includes the newest-first article feed,
URL-based search and filters, topic and source directories, and article previews
with original-publisher links. Public browsing uses the anonymous API. Optional sign-in, followed topics, and
My feed use the separate user API through a same-origin gateway.

New-account topic selection displays each page as it arrives. Each request has its
own timeout; a later failure preserves loaded topics and selections, and retry
resumes at the failed page. Saving or closing the dialog stops catalog loading.
The same onboarding component and browser regression run in the website and both
browser extensions.

My feed shows a bounded recent-article starter page while the first personalized
ranking is prepared. Direct follows are preferred; a latest-articles fallback is
labelled separately. Both obey language, content-type, source and publication
filters. Existing lists remain readable during refresh or transient errors;
readers choose **Show updates** when a new list is ready. Preparation checks run
every three seconds initially, then every five seconds, pause in hidden tabs,
and resume immediately when visible. A delayed message appears after ten seconds
or when the API reports a retry, with a latest-feed link available throughout.
These behaviors share code and browser tests with Chrome and Edge.

From the repository root, run `npm ci` then `npm run web:dev`. Set
`DEVFEED_PUBLIC_API_URL` to the API origin (default `http://127.0.0.1:8000`).
For user sign-in, also set `DEVFEED_USER_API_URL` and
`DEVFEED_USER_BASE_URL`. See [user accounts](../../docs/user-accounts.md).
Run `npm run web:lint`, `npm run web:test`, and `npm run web:build` to validate.

See [development](../../docs/development.md) and [Compose](../../docs/compose.md)
for deployment and runtime configuration.

The header sun/moon control switches light and dark themes without sign-in. A
small script applies the saved `devfeed:theme` local preference before rendering;
otherwise the site follows the operating system. Changes synchronize across tabs,
and the toggle remains usable when local storage is blocked.

Temporary article API 502/503/504 responses retain the article dialog with retry
and close controls instead of raising an unhandled page error. Temporary pages
are marked noindex; missing articles still return 404 and unexpected exceptions
still reach the error boundary. Chrome and Edge share the error content and retry
control. The browser suites exercise failure followed by successful retry.

## Reader loading and pagination

Reader pages must not exhaust a catalog or feed to render the first screen. The shared web reader and Chrome/Edge extensions use the same incremental choices and scrolling controls.

| Surface | Initial work | Continuation |
| --- | --- | --- |
| Your topics / Your sources | Render account shell; after authentication and saved preferences, request one 60-item catalog page | Scroll requests one cursor; name search starts a new server-filtered page |
| My feed onboarding | Check saved topic IDs; eligible readers request one ranked topic page | Scroll within the dialog; server-filtered name search preserves selections |
| Public topics / sources | One 60-item directory page | Scroll fetches the next page |
| Latest and filtered feeds | One feed page, bounded topic suggestions, and filter options | Feed cursor requests on scroll |
| Topic/source detail feeds | Direct item lookup, then one feed page and bounded supporting reads | Feed cursor requests on scroll; extensions never scan directories to resolve a slug |
| Article preview | One article lookup and its featured topic lookup | No catalog pagination; extension cached-article fallback uses one direct topic lookup |
| My feed / Read later / Trending | One feed page | Cursor requests on scroll; My feed refreshes its first page when active |
| Search | One search response | Independent section cursors on scroll |
| Public Markdown catalog/feed routes | One bounded page | Next-page links, without draining catalogs |

The settings server components do not preload catalogs for signed-out visitors. Saved topic/source IDs are loaded independently of visible catalog rows; saving a partially loaded selection must retain IDs outside that page. Search is debounced and applied by the public API before offset/limit, so it finds entries that have never been downloaded. Matching is case-insensitive literal name containment, including literal `%` and `_`. Existing publication, approval, enabled-source and active-topic filters still apply.

Requests have deadlines, cancel on unmount/inactivity, and deduplicate cursor loads. Replacing a search unmounts its page state and cancels the prior request. Failed pages retain already-loaded items and expose a manual retry; they do not start an automatic retry loop.

### Validation and deployment

Shared browser regression helpers test preferences, public directories, onboarding, feed/search scrolling, and article/detail navigation against the production web build and both built extensions. Preference checks record catalog requests to reject eager later-page fetches, search for an unloaded item, and retain selection after clearing search. Backend integration tests use disposable PostgreSQL and Redis to verify global search, pagination, literal wildcards, and public visibility rules.

The public API adds the optional `q` parameter to topics/sources. Deploy that API before the corresponding reader/extension release: an older API ignores `q` and cannot provide correct global catalog search. New same-origin topic/source detail endpoints must also be live before distributing the updated extensions. No database migration is needed. These source changes and local validations do not prove a production latency improvement until a separately authorized deployment is measured.

## Dev card signup preview

Anonymous feeds show a dev card modal shortly after the feed loads, waiting for
other open dialogs to close. The card reveals first; after 1.9 seconds, the modal
expands and its details slide in on the right (below the card on mobile). The
popup does not change the feed layout. The shared web/extension component rotates
and zooms the card each time the popup opens, with a light sweep, glow, brief
floating motion, and mouse tilt. Reduced-motion preferences disable movement, and readers
can dismiss the promotion for the session. Example statistics are labelled and
disappear when personalizing the preview.

Visitors can preview a display name and up to four catalog technologies before
registering. The draft stays in the original tab's session storage for up to 24
hours. Web signup returns to profile settings; extension signup uses the existing
completion tab and offers “Finish your dev card” in the original tab. Profile
settings restore the draft for review and explicit saving. This flow does not
automatically make profiles public and requires no migration or configuration.

To replay a dismissed reveal locally, clear `devfeed:dev-card-promo-dismissed`
from session storage and reload an anonymous feed. Shared Playwright coverage lives in `scripts/testing/dev-card-promo.mjs`,
invoked by the web feed browser test and the Chrome/Edge auth browser suites.
Screenshots are saved in `reports/reader-feed` and `apps/extensions/dist`.

The visual preview remains usable when authentication is unavailable, including
a local stack without OIDC configuration. Saving and signup are disabled with
an explanation until authentication is available.
