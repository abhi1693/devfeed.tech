#!/usr/bin/env bash
set -euo pipefail
# Match the four CPUs of the ARM64 CI runners when collecting coverage locally.
npm run test --workspace @devfeed/web -- --coverage --maxWorkers=4 --reporter=default --reporter=junit --outputFile=../../reports/user.xml
node node_modules/c8/bin/c8.js --all --include=scripts/ci/api-performance-changes.mjs \
  --include=scripts/ci/mutation.mjs --include=scripts/ci/mutation-runner.mjs \
  --reporter=text-summary --reporter=lcovonly --reports-dir=reports/coverage/ci-unit \
  node --test scripts/ci/api-performance.test.mjs tests/mutation.test.mjs
cat reports/coverage/ci-unit/lcov.info >> reports/coverage/web/lcov.info
python scripts/ci/check_reports.py junit reports/user.xml
