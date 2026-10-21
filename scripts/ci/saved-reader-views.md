# Saved reader views validation

This is the focused validation matrix for [#271](https://github.com/abhi1693/devfeed.tech/issues/271),
the final integration sub-issue of [#255](https://github.com/abhi1693/devfeed.tech/issues/255).
The implemented scope incorporates the subsequent product decisions: Columns belongs only to
signed-in **My feed**, uses text-only rows, and preserves an intentionally empty workspace.
Other article feeds save Grid, List, or Stacked independently. Guests keep their own browser
preferences; signing in does not import them into an account. Unified discovery search retains
its existing presentation.

## Acceptance matrix

| Requirement                                                                                                              | Shared browser coverage                           |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| Grid fallback, legacy preferences, guest/account reconciliation, and cross-tab API restoration                           | `reader-preferences.mjs` and `feed-views.mjs`     |
| Independent semantic feed layouts, canonical aliases, filters, failed layout saves, and preview retention                | `feed-views.mjs`                                  |
| Stacked thumbnails, missing metadata, long text, pagination, and shared article actions                                  | `stacked-reader.mjs`                              |
| Add, edit, reorder, remove, deliberate clearing, unavailable selections, and keyboard/mobile navigation                  | `columns-workspace.mjs`                           |
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
have backend regressions in `tests/test_user_columns_settings.py`, `tests/test_user_feed_views.py`,
and `tests/test_user_column_starters.py`.

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

Local runs made before committing correctly record a dirty source tree; they must not be
presented as hosted checks of the later commit. Hosted checks and CodeQL remain separate merge
requirements. Automated accessibility results complement keyboard and visual checks and do not
establish full WCAG compliance.
