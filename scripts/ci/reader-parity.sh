#!/usr/bin/env bash
set -euo pipefail
# Edge is available on Linux x86_64; this CI job uses the matching runner.
npx --no-install --ignore-scripts playwright install --with-deps chromium msedge
status=0
run_check() {
  if "$@"; then return 0; else status=1; fi
}
run_check node --test apps/web/tests/browser/accessibility.browser.mjs
if npm run web:build; then
  run_check node tests/benchmarks/reader-history.mjs
  run_check node apps/web/tests/browser/feed.mjs
  run_check node apps/web/tests/browser/telemetry.mjs
  run_check node apps/web/tests/browser/x-pixel.mjs
  run_check node apps/web/tests/browser/profile.mjs
else
  status=1
fi
# Attempt each runtime even when another fails, so its report is still available.
run_check npm run extension:test:browser
run_check npm run extension:edge:test:browser
run_check env DEVFEED_HISTORY_PLATFORM=chrome node tests/benchmarks/reader-history.mjs
run_check env DEVFEED_HISTORY_PLATFORM=edge node tests/benchmarks/reader-history.mjs
exit "$status"
