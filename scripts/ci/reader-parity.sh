#!/usr/bin/env bash
set -euo pipefail
suite=${1-all}
if (( $# > 1 )); then
  echo 'Usage: reader-parity.sh [all|web|chrome|edge|partner]' >&2
  exit 2
fi
case "$suite" in
  all|web|chrome|edge|partner) ;;
  *) echo "Unknown reader suite: $suite" >&2; exit 2 ;;
esac

status=0
group=setup
timings=()
print_summary() {
  local result=$1
  local heading="Reader parity: $suite (exit $result)"
  printf '\n%s\n' "$heading"
  printf '| Group / command | UTC start | Duration (s) | Exit |\n'
  printf '| --- | --- | ---: | ---: |\n'
  printf '%s\n' "${timings[@]}"
  if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
    {
      printf '\n### %s\n\n' "$heading"
      printf '| Group / command | UTC start | Duration (s) | Exit |\n'
      printf '| --- | --- | ---: | ---: |\n'
      printf '%s\n' "${timings[@]}"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

run_timed() {
  local started_at started_seconds command code duration
  started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  started_seconds=$SECONDS
  printf -v command '%q ' "$@"
  printf '[%s] START %s: %s\n' "$started_at" "$group" "$command"
  if "$@"; then code=0; else code=$?; fi
  duration=$((SECONDS - started_seconds))
  printf '[%s] END %s: exit=%s duration=%ss %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$group" "$code" "$duration" "$command"
  timings+=("| $group / \`$command\` | $started_at | $duration | $code |")
  return "$code"
}

run_check() {
  if run_timed "$@"; then return 0; else status=1; fi
}

web_checks() {
  run_check node --test apps/web/tests/browser/accessibility.browser.mjs
  if run_timed npm run web:build; then
    run_check node apps/web/tests/browser/document-streaming.mjs
    run_check node tests/benchmarks/reader-history.mjs
    run_check node apps/web/tests/browser/home-entry.mjs
    run_check node apps/web/tests/browser/structured-data.mjs
    run_check node --test apps/web/tests/browser/feed.mjs
    run_check node apps/web/tests/browser/telemetry.mjs
    run_check node apps/web/tests/browser/x-attribution.mjs
    run_check node apps/web/tests/browser/profile.mjs
    run_check node apps/web/tests/browser/reader-gateway.mjs
  else
    status=1
  fi
}

chrome_checks() {
  run_check npm run extension:test:browser
  run_check env DEVFEED_HISTORY_PLATFORM=chrome node tests/benchmarks/reader-history.mjs
}

edge_checks() {
  run_check npm run extension:edge:test:browser
  run_check env DEVFEED_HISTORY_PLATFORM=edge node tests/benchmarks/reader-history.mjs
}

partner_checks() {
  local ready=1
  # This suite runs on its own runner, so it cannot rely on another suite's output.
  if ! run_timed npm run web:build; then ready=0; status=1; fi
  if ! run_timed npm run extension:build; then ready=0; status=1; fi
  if ! run_timed npm run extension:edge:build; then ready=0; status=1; fi
  if (( ready )); then
    run_check node apps/web/tests/browser/partner-tracking.mjs
    run_check node apps/web/tests/browser/partner-placements.mjs
  fi
}

# Edge is available on Linux x86_64; these CI suites use the matching runner.
case "$suite" in
  web|chrome) browsers=(chromium) ;;
  *) browsers=(chromium msedge) ;;
esac
if run_timed npx --no-install --ignore-scripts playwright install --with-deps "${browsers[@]}"; then
  :
else
  code=$?
  print_summary "$code"
  exit "$code"
fi

for group in web chrome edge partner; do
  if [[ $suite == all || $suite == "$group" ]]; then
    case "$group" in
      web) web_checks ;;
      chrome) chrome_checks ;;
      edge) edge_checks ;;
      partner) partner_checks ;;
    esac
  fi
done
print_summary "$status"
exit "$status"
