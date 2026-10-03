#!/usr/bin/env bash
set -euo pipefail
npm run test:dependency-security
npm run test --workspace @devfeed/web -- --reporter=default --reporter=junit --outputFile=../../reports/user.xml
python scripts/ci/check_reports.py junit reports/user.xml
