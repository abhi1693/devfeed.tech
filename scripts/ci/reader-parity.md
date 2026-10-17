# Reader browser suites

Run `bash scripts/ci/reader-parity.sh` for all reader checks, or pass `web`,
`chrome`, `edge`, or `partner` to select an isolated CI suite. Invalid arguments
fail before browser installation. Commands run sequentially within each suite;
CI runs the suites on separate runners, never concurrently in one checkout.

The web suite retains its production-build guard and all web checks. Chrome and
Edge retain their built-extension journeys and history benchmarks. The partner
suite builds web and both extensions before running the unchanged tracking and
placement programs, including all three runtimes, viewports, and session states.
Its builds are necessary even when the other CI suites have already built their
own outputs. The default local command also performs those prerequisites.

A failed check marks the suite failed while remaining independent checks still
run. A failed web build skips dependent web checks; a failed partner prerequisite
skips the two partner programs after attempting every prerequisite. Browser
installation failure stops the suite. Assertions and browser timeouts remain in
their original programs.

Every command prints UTC start/end timestamps, duration, and exit status. The
final table also appears in `GITHUB_STEP_SUMMARY` when available. These durations
measure that runner's commands; compare complete hosted runs before claiming a
CI latency improvement. Setup and duplicated builds can offset parallelism.

Run `node --test tests/reader-parity.test.mjs` to check suite isolation, the full
original command inventory, prerequisite guards, and failure accumulation with
fake command executors. Real web, Chrome, Edge, and partner browser checks remain
required independently of these dispatch tests.
