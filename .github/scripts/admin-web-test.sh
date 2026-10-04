#!/usr/bin/env bash
set -euo pipefail
# Match the four CPUs of the ARM64 CI runners when collecting coverage locally.
npm run test --workspace @devfeed/admin -- --coverage --maxWorkers=4 --reporter=default --reporter=junit --outputFile=../../reports/admin.xml
python scripts/ci/check_reports.py junit reports/admin.xml
