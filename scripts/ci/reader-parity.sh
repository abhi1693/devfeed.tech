#!/usr/bin/env bash
set -euo pipefail
# Edge is available on Linux x86_64; this CI job uses the matching runner.
npx playwright install --with-deps chromium msedge
npm run reader:test:parity
node apps/web/tests/browser/telemetry.mjs
node apps/web/tests/browser/x-pixel.mjs
node apps/web/tests/browser/profile.mjs
