# Reader browser suites

The [saved reader views matrix](saved-reader-views.md) maps the four layouts to their
composed account, keyboard, accessibility, motion, and resource-budget checks. Its evidence
is retained by these existing reader workers and artifact uploads.

Run `bash scripts/ci/reader-parity.sh` for the original full local sequence. Pass
`web`, `chrome`, `edge`, or the legacy `partner` selector to run that group alone.
The six isolated CI suites are `web`, `chrome`, `edge`, `partner-web`,
`partner-chrome`, and `partner-edge`. Invalid arguments fail before browser
installation. Every suite runs its commands sequentially on its own runner;
programs never race over build output in a shared checkout.

CI displays these as **Reader browser (Web)**, **Reader browser (Chrome extension)**,
**Reader browser (Edge extension)**, **Reader browser (Partner web)**,
**Reader browser (Partner Chrome)**, and **Reader browser (Partner Edge)**.
**Reader browser gate** requires all six workers. **Admin browser tests** and
**Live API browser tests** remain separate jobs. Their job IDs and dependencies
remain unchanged.

The web suite retains its production-build guard and every original web check.
Chrome and Edge retain their built-extension journeys and history benchmarks.
The three partner workers each build the web app because every runtime uses its
real production Next.js gateway. Partner Chrome and Partner Edge also build their
own extension. Partner web builds both extensions and runs the unchanged tracking
program across all three runtimes once, followed by the full web placement matrix.
The other partner workers run the complete placement matrix for their runtime.
All existing feature cases, viewports, session states, assertions and timeouts stay
inside those matrices. No worker relies on another runner's build output.

The default local `all` and legacy `partner` selectors retain the original partner
sequence: fresh web and both extension builds, full tracking, and one full
three-runtime placement program. The placement program also accepts
`--runtime=web`, `--runtime=chrome`, or `--runtime=edge`; without a selector it
keeps all three full runtime matrices. Existing focused flags remain supported.

## Preserve same-gateway caller isolation

A selected-runtime placement run first opens three short, fresh browser contexts
against the **same live Next.js gateway process**. Each uses the selected browser
and built extension, navigates a real article placement, scrolls it into view,
and waits for its signed impression. The trusted fixture edge changes caller IP
from `203.0.113.20` to `.30` to `.40`. Both placement-query and tracking proofs must
contain exactly that context's caller IP. The context closes before the next IP
is selected, and fixture state resets before the full feature matrix.

This triplet catches a gateway that incorrectly caches its first caller identity;
three independent workers with one caller each would not catch that bug. Caller
IPs enter the cumulative evidence only through the existing HMAC verifier. The
original final assertion requiring three distinct anonymous callers remains
unconditional. Default full local runs already switch all three browser callers
on one gateway, so they do not add the lightweight probes.

Successful full placement runs record the actual caller/probe observations,
completed runtime count, and completed feature checkpoints under
`reports/partner-placements/runtime-proof-<runtime>.json`. The report is written
only after browser contexts close, the full feature matrix completes, and the
three-caller assertion passes. Focused runs cannot emit a full-completion proof.
The aggregate requires all three selected-runtime reports with the current source
SHA and run attempt, exact caller and endpoint sets, complete same-gateway probes,
and every completion checkpoint. It also requires every worker success marker
and the original six accessibility summaries (guest and signed-in Web/Chrome/Edge).
Artifact directories remain separate for every producer.

A failed check keeps its suite red while remaining independent checks still run.
Every prerequisite build is attempted, but any failed prerequisite prevents its
dependent partner programs from using stale output. Browser installation failure
stops the suite. Per-command UTC timing tables remain in the console and
`GITHUB_STEP_SUMMARY`. Placement logs add `BEGIN` and successful `END` markers for
each actual caller probe and full runtime matrix.

Run `node --test tests/reader-parity.test.mjs` for dispatch, parser, command inventory,
prerequisite, and failure-accumulation regressions with fake executors. The same
suite checks the cached-first-caller rejection predicate; CI runs it independently
of the heavy browsers. Functional Python CI contracts execute the actual aggregate
script against missing, stale, partial and cross-caller-invalid reports. Real
browser runs remain required independently of these checks.

Compare complete hosted runs before claiming a latency improvement. Fresh builds,
browser setup, and runner availability can offset parallelism. This split changes
scheduling; it does not relax test budgets, assertions, coverage, or failure gates.

## Workflow dependency boundaries

The six reader workers run independently; the reader gate waits for the entire
matrix and validates all six current-source, current-attempt reports. Coverage
comment jobs still wait for their own producers. Performance and browser-budget
workflows are retired; reader correctness and accessibility checks remain required.

Sonar retains its eleven actual coverage-producer and availability-output
dependencies. It does not consume reader, admin-browser, or live-browser reports,
so those jobs do not delay coverage analysis. `CI required` still waits for the
strict reader gate and every original final check. The separate [release workflow](release.md)
requires that exact commit’s successful master CI attempt before building and publishing
release images. Tag runs reuse the master reader proof rather than repeating the matrix.
The scheduling changes add no inter-worker dependencies and remove no coverage or
final-merge gate.

Sponsored-column checks stop the prior reader document before installing fixtures,
so their delivery counts cover only the five-column workspace under test.
