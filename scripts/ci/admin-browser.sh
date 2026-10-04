#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
npx playwright install --with-deps chromium
npm run admin:build
for test in apps/admin/tests/browser/*.mjs; do
  echo "Running $test"
  node "$test"
done
node apps/web/tests/browser/telemetry.mjs admin
