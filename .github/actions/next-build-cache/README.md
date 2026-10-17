# Next.js compiler cache

Call this local action after checkout and Node setup, before the existing production build:

```yaml
- uses: ./.github/actions/next-build-cache
  with:
    app: web
    profile: production
```

`app` accepts `web`, `admin`, or `partner`. Use a stable lowercase `profile` for builds with
different environment settings, such as `production`, `browser-budgets`, or `dast`. Builds
inside a test command need the action before that command too.

The key separates applications, profiles, the actual Node version, platform, architecture,
the root npm lockfile, and root/application build configuration. The commit SHA makes each
source revision a new cache entry. A source-only change can restore an earlier compiler
cache with the same compatibility prefix. A dependency, configuration, runtime, application,
or profile change cannot restore that earlier cache.

Only Turbopack, webpack, SWC, and Next's TypeScript incremental metadata are cached.
The action excludes runtime fetch and image data, credentials, complete `.next` output,
standalone servers, static output, source maps, reports, and browser profiles. It neither
creates empty cache directories nor skips any build or test on a cache hit. A first cold
build can still report that no build cache was found.

The pinned cache action saves compiler data after a successful job. Normal GitHub cache
branch and pull-request scope restrictions apply. Cache contents must contain no secrets.
BuildKit layer caches remain separate; this action does not populate Docker cache mounts.

Run the regression suite with `node --test tests/next-build-cache.test.mjs`. For a build
proof, run an unchanged production build cold, preserve only the listed compiler paths,
remove the remaining production output, restore those paths, and run a fresh build again.
Check both builds and runtime behavior; a cache hit must not substitute for a new build.
Also verify that changing source retains only the compatibility fallback, while changing
the lockfile, build configuration, app, profile, Node version, or platform changes that
fallback. Compare build durations separately from browser performance measurements.

See [Next.js CI caching](https://nextjs.org/docs/app/guides/ci-build-caching) and
[GitHub cache restrictions](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache).
