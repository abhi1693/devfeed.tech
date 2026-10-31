# Reader Markdown syntax reuse

The shared Markdown component retains only serialized syntax from one fixed GFM
parser configuration. React elements, HTML, nonces, authorization, and article
publication decisions are not cached. Every render still runs the existing
HTML/image suppression, URL policy, and compact/full link rendering. Current
article data must still pass the normal API checks before rendering.

The cache is local to each server process or browser realm, with at most 128
entries and 2 MiB of UTF-16 string payload, plus map overhead. Sources above 16 Ki
characters and oversized syntax trees bypass retention. Content itself is the
key: changed text reparses immediately. Hits deserialize a fresh tree so downstream
plugins cannot mutate another render. Eviction uses least-recent access without
background timers. This optimization also applies to both browser extensions and
shared administration/partner Markdown consumers.

## Local measurement

Node 24, x86-64, production-style `react-dom/server` static rendering, median CPU
of three 250-render samples after JIT warm-up. Each 699-character fixture contains
GFM tables, links, emphasis, lists and fenced code. These are component-level
measurements, not whole-web or production savings.

| Workload                     | Baseline CPU | Syntax cache CPU |
| ---------------------------- | -----------: | ---------------: |
| 250 renders of the same text |     740.1 ms |         267.9 ms |
| 250 distinct texts           |     708.1 ms |         687.9 ms |

Repeated text used about 64% less component CPU. The unique control had no cache
hits, so it has no expected speedup and still pays serialization overhead. Review
reruns varied from about 4% faster to 18% slower for unique text; this benchmark
does not establish the miss-path cost. The retained payload stayed below 2 MiB
and entry/byte/oversized-input tests verify the bounds.

Reproduce from the repository root:

```sh
node apps/web/tests/markdown.benchmark.mjs
npm run test --workspace @devfeed/web -- --run tests/markdown.test.tsx tests/markdown-cache.test.tsx
```

The warm-hit ratio determines actual benefit. Native ARM64, representative crawler
traffic and end-to-end CPU/request need canary measurement before claiming a
production gain. Do not extend this into a whole-page/response cache: authenticated
state, CSP nonces and publication/source withdrawal checks must remain fresh.
