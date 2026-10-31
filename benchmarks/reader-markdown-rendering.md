# Reader Markdown syntax reuse

The shared Markdown component retains only serialized syntax from one fixed GFM
parser configuration. React elements, HTML, nonces, authorization, and article
publication decisions are not cached. Every render still runs the existing
HTML/image suppression, URL policy, and compact/full link rendering. Current
article data must still pass the normal API checks before rendering.

The cache is local to each server process or browser realm, with at most128
entries and2MiB of UTF-16 string payload, plus map overhead. Sources above16Ki
characters and oversized syntax trees bypass retention. Content itself is the
key: changed text reparses immediately. Hits deserialize a fresh tree so downstream
plugins cannot mutate another render. Eviction uses least-recent access without
background timers. This optimization also applies to both browser extensions and
shared administration/partner Markdown consumers.

## Local measurement

Node24, x86-64, production-style `react-dom/server` static rendering, median CPU
of three250-render samples after JIT warm-up. Each699-character fixture contains
GFM tables, links, emphasis, lists and fenced code. These are component-level
measurements, not whole-web or production savings.

| Workload                    | Baseline CPU | Syntax cache CPU |
| --------------------------- | -----------: | ---------------: |
| 250renders of the same text |      740.1ms |          267.9ms |
| 250distinct texts           |      708.1ms |          687.9ms |

Repeated text used about64% less component CPU. The unique control had no cache
hits; its small timing difference is noise, not an expected speedup. The retained
payload stayed below2MiB and entry/byte/oversized-input tests verify the bounds.

Reproduce from the repository root:

```sh
node apps/web/tests/markdown.benchmark.mjs
npm run test --workspace @devfeed/web -- --run tests/markdown.test.tsx tests/markdown-cache.test.tsx
```

The warm-hit ratio determines actual benefit. NativeARM64, representative crawler
traffic and end-to-end CPU/request need canary measurement before claiming a
production gain. Do not extend this into a whole-page/response cache: authenticated
state, CSP nonces and publication/source withdrawal checks must remain fresh.
