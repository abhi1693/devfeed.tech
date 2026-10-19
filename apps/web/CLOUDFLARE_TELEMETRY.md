# Cloudflare reader telemetry assessment

Issue [#134](https://github.com/abhi1693/devfeed.tech/issues/134) concerns an initial
document → Cloudflare beacon → `/cdn-cgi/rum` dependency chain. The October 7
Lighthouse audit estimated zero LCP savings from the chain; its presence and cache
diagnostic alone do not establish render blocking.

## Ownership and current behavior

On October 10, 2026, actual anonymous Chromium visits to `/latest` and `/topics`
received exactly one nonce-authorized `type="module"` script from
`static.cloudflareinsights.com`. The same deployed website fetched directly
through its Kubernetes service contained no beacon script on either route. The
application and ingress manifests contain no Cloudflare beacon initialization.
This establishes edge injection; the Cloudflare dashboard's particular enabled
product/settings were not inspected or changed.

Cloudflare documents automatic injection through Web Analytics or Observatory
and a dashboard-managed manual-installation alternative. See its
[RUM beacon documentation](https://developers.cloudflare.com/speed/observatory/rum-beacon/)
and [setup options](https://developers.cloudflare.com/web-analytics/get-started/).
The application cannot set attributes on an edge-injected element before its
request starts. A client effect or MutationObserver runs too late to control that
initial fetch reliably.

The observed script is a module, even though its `async` and `defer` properties
are false. Module execution already waits for parsing under the
[HTML script processing model](https://html.spec.whatwg.org/multipage/scripting.html#the-script-element).
Do not classify it as a parser-blocking classic script from those two properties
alone. Chromium nevertheless assigns its fetch High priority. RUM requests also
had High priority, and started after the document's load event in all 12 retained
samples. Every retained sample delivered RUM successfully with HTTP 204 and no
Cloudflare CSP violations.

The beacon transferred 10,438 encoded bytes per cold sample and returned
`Cache-Control: public, max-age=86400`. These are observed vendor headers, not
DevFeed cache settings. A longer lifetime, an application proxy, extra preconnects,
or a privacy-header change is not justified by this finding.

## Comparison — October 10, 2026

The paired experiment used the unchanged production `0.0.51` website through the
public Cloudflare edge, Chromium 153.0.8010.12, and three fresh contexts per route,
form factor, and condition: 24 navigations total. Each pair alternated retained
versus browser-blocked beacon order. Browser cache was disabled. Desktop used
1350×940, DPR 1, without CPU/network throttling. Mobile used 412×823, DPR 1.75,
4× CPU slowdown, 150ms latency, 1.6Mbps download, and 750Kbps upload through CDP.
These are actual throttled traces, not Lighthouse's simulated performance model.
No other production analytics or API behavior was changed.

Medians, milliseconds:

| Route     | Form factor | FCP retained → blocked | Observed LCP retained → blocked | Post-load reader check retained → blocked |
| --------- | ----------- | ---------------------- | ------------------------------- | ----------------------------------------- |
| `/latest` | Desktop     | 1236 → 656             | 1408 → 656                      | 1885 → 878                                |
| `/topics` | Desktop     | 316 → 324              | 316 → 324                       | 388 → 424                                 |
| `/latest` | Mobile      | 784 → 788              | 784 → 788                       | 3468 → 3425                               |
| `/topics` | Mobile      | 708 → 592              | 708 → 592                       | 2327 → 2162                               |

The post-load check waits for an actual article/topic link after `load`; it is not
a time-to-interactive metric or the first moment a reader can use the page. LCP
is the observed candidate after a four-second post-load window, not field p75.

Blocking removes the beacon transfer and all RUM delivery. This is a diagnostic
counterfactual, not an implemented improvement or an acceptable rollout: it loses
telemetry coverage. The mixed timing differences are not a causal performance
estimate. In particular, every retained `/latest` sample started its beacon fetch
**after FCP**, so the large desktop FCP difference cannot be caused by blocking
that later fetch. Retained desktop `/latest` FCP ranged from 264 to 1696ms on the
live site. Topic samples show possible early network competition worth measuring
again if Cloudflare offers an adjustable priority policy, but these three samples
do not establish a coverage-preserving intervention.

## Decision and follow-up

Retain the current edge instrumentation. There is no application initializer to
defer, and no verified application change that removes these requests while
preserving required coverage. This follows the issue's externally controlled
policy acceptance path. The regression changes protect against adding a duplicate
origin/extension beacon and verify the existing first-party Faro collector still
delivers telemetry. No runtime analytics settings or privacy headers are changed.

If future traces establish a meaningful bottleneck, inspect the site's Web
Analytics and Observatory settings first. A migration to Cloudflare's supported
manual setup requires coordinated removal of automatic injection and validation
of initial visits, short visits, SPA navigation, page-hide delivery, Web Vitals,
nonce/CSP compatibility, and absence of duplicates. An arbitrary idle timeout
would risk missing short visits and is not supported by this evidence. Do not
silently disable RUM or add a manual snippet while automatic injection remains on.

## Reproduce

From the repository root, with Playwright Chromium installed:

```sh
node tests/benchmarks/cloudflare-telemetry.mjs
```

This reads public pages and allows their normal telemetry requests. It does not
sign in, create accounts, or change Cloudflare/Kubernetes configuration. Optional
`DEVFEED_CF_TARGET` chooses another public origin and `DEVFEED_CF_RUNS` chooses
1–10 samples per condition. Use a quiescent host and run sequentially.

To also compare the deployed origin without the edge, use a temporary read-only
service port-forward, then stop it after the run:

```sh
kubectl -n devfeed port-forward service/devfeed-web 31334:3000
DEVFEED_CF_ORIGIN=http://127.0.0.1:31334 node tests/benchmarks/cloudflare-telemetry.mjs
```

The harness asserts one injected script, successful retained RUM delivery, no
Cloudflare CSP violations, no RUM in the blocked counterfactual, usable reader
links, and no origin duplicate when the origin option is supplied. It records
priorities, relative request timings, encoded bytes, cache policy, paint/load
metrics, and script type. Reports remain ignored under
`reports/cloudflare-telemetry`; they exclude HTML, full request URLs, query strings,
request bodies, cookies, nonce values, and beacon/visitor identifiers. Do not run
this production diagnostic as a deterministic CI performance gate.

The ordinary CI telemetry browser test verifies the local origin emits no
Cloudflare requests while first-party Faro delivery succeeds. The built Chrome
and Edge reader suites also assert no Cloudflare requests across their navigation
journeys: local extension documents are not served through the Cloudflare edge.
