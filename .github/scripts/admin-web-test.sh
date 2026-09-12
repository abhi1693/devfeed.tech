#!/usr/bin/env bash
set -euo pipefail
npm run test --workspace @devfeed/admin -- --reporter=default --reporter=junit --outputFile=../../reports/admin.xml
python scripts/ci/check_reports.py junit reports/admin.xml
