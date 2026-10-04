#!/usr/bin/env bash
set -euo pipefail
# Match the four CPUs of the ARM64 CI runners when collecting coverage locally.
npm run test --workspace @devfeed/web -- --coverage --maxWorkers=4 --reporter=default --reporter=junit --outputFile=../../reports/user.xml
python scripts/ci/check_reports.py junit reports/user.xml
