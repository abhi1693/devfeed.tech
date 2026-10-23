# Saved reader views validation

This is the focused validation matrix for [#271](https://github.com/abhi1693/devfeed.tech/issues/271),
the final integration sub-issue of [#255](https://github.com/abhi1693/devfeed.tech/issues/255).
The implemented scope incorporates the subsequent product decisions: Columns belongs only to
signed-in **My feed**, uses text-only rows, and preserves an intentionally empty workspace.
Other article feeds save Grid, List, or Stacked independently. Guests keep their own browser
preferences; signing in does not import them into an account. Unified discovery search retains
its existing presentation.

Signed-in layouts and ordered column definitions are stored in PostgreSQL
`user_accounts.feed_settings`, scoped to the account. A new device loads these settings
after sign-in, including a deliberately empty workspace. Guest browser storage is kept
separate. An already-open device picks up changes from another device after reloading.

Use the grip in a column header to drag it into a new position. With the grip focused,
Space or Enter picks up the column, Left/Right moves it, Space or Enter drops it, and
Escape cancels. Dropping saves the order once; a failed save restores the previous order.
Reordering preserves the loaded articles, individual reading positions, and active reading
sequence. On narrow screens, dragging toward the board edge scrolls to more columns.
The column menu also offers **Move to position** for click/tap-only reordering.

## Acceptance matrix

| Requirement                                                                                                              | Shared browser coverage                           |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| Grid fallback, legacy preferences, guest/account reconciliation, and cross-tab API restoration                           | `reader-preferences.mjs` and `feed-views.mjs`     |
| Independent semantic feed layouts, canonical aliases, filters, failed layout saves, and preview retention                | `feed-views.mjs`                                  |
| Stacked thumbnails, missing metadata, long text, pagination, and shared article actions                                  | `stacked-reader.mjs`                              |
| Add, edit, reorder, remove, deliberate clearing, unavailable selections, and keyboard/mobile navigation                  | `columns-workspace.mjs`                           |
| Pointer, touch, and keyboard reordering, cancellation, failed saves, reading retention, and persisted order              | `columns-reordering.mjs`                          |
| Personalized first-use columns without overwriting configured workspaces                                                 | `columns-personal.mjs` and `columns-starters.mjs` |
| Guest → account A → account B → A → guest with rendered workspaces and a late A feed response                            | `reader-views-lifecycle.mjs`                      |
| Failed column save retains pages, DOM, scroll, and draft; keyboard retry persists without changing another feed's layout | `reader-views-recovery.mjs`                       |
| All supported layouts in light/dark desktop/mobile, WCAG checks, keyboard focus, and observed reduced motion             | `reader-views-accessibility.mjs`                  |
| Independent pages, overlapping article actions, partial failures, stale responses, and request/render limits             | `columns-feeds.mjs`                               |
| Sponsored delivery, tracking, deduplication, and preview/pagination stability                                            | `columns-sponsored.mjs`                           |
| Readable long content, bounded sponsor copy, touch controls, and no page-level overflow                                  | `columns-scanning.mjs`                            |

These helpers run through the production web app and both installed, built browser extensions.
They mock local API fixtures, so they do not prove production deployment, identity provider
configuration, or extension store publication. Account API authorization and persistence also
have backend regressions in `tests/users/reader/views/test_columns_settings.py`, `tests/users/reader/views/test_feed_views.py`,
and `tests/users/reader/views/test_column_starters.py`. `tests/users/reader/views/test_reader_view_persistence.py` uses
independent authenticated sessions and fresh app clients to verify restoration from committed
PostgreSQL records, without browser storage or overridden authentication.

Columns retains at most 12 selections and 72 organic articles per column (three pages of 24),
or 864 organic rows in a full workspace. Feed, catalog, and product-delivery requests share a
three-request queue. The bounded-feed journey measures those limits rather than substituting
implementation constants for browser observations.

## Reproduce locally

Build the current app and both extension packages first:

```sh
npm run web:build
npm run extension:build:all
```

Run the composed account, failed-save, accessibility/motion, and budget checks:

```sh
node --test --test-name-pattern='saved reader views|columns feeds load independently' apps/web/tests/browser/feed.mjs
node --test --test-name-pattern='saved reader views|columns feeds load independently' apps/extensions/tests/auth.browser.mjs
DEVFEED_EXTENSION_BROWSER=edge node --test --test-name-pattern='saved reader views|columns feeds load independently' apps/extensions/tests/auth.browser.mjs
```

For the complete feature regression matrix, use the pattern
`saved reader views|columns |feed layouts persist|reader preferences persist|stacked reader preserves|article layouts save`
with the same three commands. Node's name filter alone does not establish that every test ran;
retain the actual test results and inspect the scenario reports. The full CI reader suites run
the complete browser files without a name filter.

## Evidence and CI

The existing web, Chrome, and Edge workers invoke these journeys through `feed.mjs` and
`auth.browser.mjs`; the required reader gate still waits for their successful worker results.
No additional browser job or alternate merge gate is introduced.

New composed evidence lives under ignored `reports/reader-views/<runtime>/<scenario>/`, which
the existing reader artifact upload already collects. The scenarios are `account-lifecycle`,
`save-recovery`, `accessibility-guest`, `accessibility-member`, and `column-budgets`. Set
`DEVFEED_READER_VIEWS_REPORT_DIR` to put local captures elsewhere, such as `/tmp`.

Each `summary.json` records its observed running/passed/failed status, checked-out Git revision,
dirty-worktree flag, runtime, scenario, and CI run attempt. A mismatched `GITHUB_SHA` is rejected.
Accessibility reports include individual Axe results, measured motion, save-toast placement,
keyboard dismissal, and screenshots; budget
reports retain the existing `observations.json` and failure captures. Separate runtime directories
keep evidence from overwriting another browser's results. Reports contain public fixture identities
and observations, not authentication headers or session credentials.

Other reader feature journeys allocate a fresh owner-only (`0700`) artifact directory with
`mkdtemp` and print its location in the test log. Local runs retain it under the operating
system's temporary directory; CI runs retain it under `reports/reader-artifacts/`, which the
existing artifact upload includes. Each run has a random directory name, so parallel runs do
not overwrite evidence and no screenshot or JSON file uses a predictable shared `/tmp` path.
These directories are intentionally retained for inspection; remove only the printed run
directory when its evidence is no longer needed.

`DEVFEED_COLUMNS_SCANNING_OUTPUT` now names an artifact **parent directory**, rather than an
exact output prefix. The scanning journey creates a private random child beneath that parent
and prints the actual location. Existing scripts that read a fixed `<prefix>.json` or PNG
path must instead use the printed run directory. This does not change the source-bound
`reports/reader-views/<runtime>/<scenario>/` evidence locations above.
An explicit parent owned by another user or world-writable without sticky-directory
protection is rejected. Existing checkout report-directory permissions are left intact;
each allocated run directory remains owner-only.

Local runs made before committing correctly record a dirty source tree; they must not be
presented as hosted checks of the later commit. Hosted checks and CodeQL remain separate merge
requirements. Automated accessibility results complement keyboard and visual checks and do not
establish full WCAG compliance.
