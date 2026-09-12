# Challenge solvers

Source profiles, original article content/metadata, and image discovery share the
same optional solver interface. Ordinary public fetches run first. An explicit
Cloudflare `cf-mitigated: challenge` or AWS WAF challenge/CAPTCHA header identifies
a challenge; HTTP 403, 202, 429 or 5xx alone does not.

With `DEVFEED_SOLVER_QUEUE_ENABLED=true`, a challenged enrichment job is returned
to the durable PostgreSQL outbox with `requires_solver=true`. It keeps its ID,
partial progress and history. The initial handoff does not consume a solver attempt.
Normal ingestion/analysis dispatch excludes these rows; scheduler and immediate
dispatch publish them only to `solver`. Recovery preserves that routing flag.
Each of the three enrichment pipelines uses the same handoff policy. Feed admission,
RSS ingestion and AI citation verification retain their existing direct fetch paths.

## One dedicated worker

Run `devfeed worker --queue solver` with one replica. A solver worker only consumes
that queue; `all`, `background` and `analysis` never consume it. A process configured
with solver services is rejected if started on a general queue. General/AI workers
need only the routing flag; they receive no solver endpoint or browser packages.
The worker remains a normal backend process. Chromium runs in the remote service.
For pending source suggestions in full automation mode, the dedicated worker also
needs the existing remote Codex settings to finish relevance assessment; those
credentials never go to solver providers.

Only the dedicated worker receives these settings:

```dotenv
DEVFEED_SOLVER_QUEUE_ENABLED=true
DEVFEED_SOLVER_TIMEOUT_SECONDS=45
DEVFEED_SOLVER_SERVICES=[{"provider":"flaresolverr","url":"http://solver-a:8191","timeout_seconds":30},{"provider":"flaresolverr","url":"http://solver-b:8191","timeout_seconds":15}]
```

The ordered service list accepts up to four instances. `provider`, `url` and
`timeout_seconds` are generic service fields. The initial adapter implements
[FlareSolverr's `/v1` `request.get` protocol](https://github.com/FlareSolverr/FlareSolverr#requestget); adding a different provider requires
implementing `Solver.solve(SolveRequest) -> FetchResult`, registering its adapter
and adding its configuration discriminator. No arbitrary module loading is allowed.
A successful result passes shared public-destination, content-type, body-size and
remaining-challenge checks before enrichment can use it. Provider cookies and raw
error messages are neither persisted nor exposed to callers.

Services are tried in order within one overall time budget. A known connection
failure or completed unsuccessful solve can fail over to another service. After
an uncertain timeout/disconnect, another remote browser is not launched: the shared
lease is held until expiry. This prevents overlapping DevFeed solves even during
worker replacement. Busy/unavailable services use the existing bounded retry policy.
An exhausted unresolved challenge remains a failed job, not a successful extraction.
The admin worker page includes the solver queue and its durable backlog.

## Isolation and limits

Solver origins are operator-controlled configuration, never request input. They
must remain internal and enforce public-only HTTP(S) egress, denying private,
loopback, link-local, reserved and cluster destinations. DevFeed validates public
DNS before requesting a solve and checks the returned URL again, but those checks
cannot police redirects and subresources inside a remote browser. Upstream TLS
checking and browser isolation depend on the provider; the guarded direct transport
continues to validate TLS and pin public DNS. Do not treat the service as an
unrestricted authenticated browser or expose it publicly.

HTML byte limits remain specific to each enrichment type. JSON transport is bounded
separately to allow escaped HTML. No user login, persistent session or CAPTCHA token
is sent. Solver jobs have a 240-second RQ timeout within their 300-second lease.
FlareSolverr reports a synthetic HTTP 200, so returned challenge/error pages are
checked explicitly. It may load Cloudflare-protected pages while leaving an AWS
challenge unresolved; support for every interactive CAPTCHA is not implied.

## Release and recovery

The feature requires additive schema revision `0002` and a matching application
release. It defaults off and does not automatically resubmit historical terminal
failures. After deployment, use the existing retry action for affected jobs. Keep
the scheduler routing flag enabled while the solver queue is in use. Disabling
solver dispatch pauses its queued rows; it does not move them back to general workers.

For DevFeed's September 12 production diagnosis, Medium-hosted source pages and
OpenAI returned explicit Cloudflare 403 challenges. Ars Technica returned an AWS
202 challenge, while both JetBrains sites had historical 202 failures and currently
loaded successfully. Google Developers and Laravel advertised XML feed URLs as
website links. Source enrichment now tries the origin root once for a non-HTML
website response, preserves existing profile values, and reports the failing
resource and HTTP status instead of an opaque `http_error` alone.
