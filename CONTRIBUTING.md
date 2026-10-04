# Contributing to DevFeed

Contributions are welcome: bug fixes, accessibility improvements, clearer
documentation, tests and features that help developers find useful content.

For a substantial feature or architectural change, open an issue first to discuss
the problem and proposed behavior. Small, focused fixes can go directly to a pull
request. Report vulnerabilities privately using [SECURITY.md](SECURITY.md).

## Get started

1. Fork the repository and create a branch from `master`.
2. Install Python 3.12+, uv and Node.js 24 LTS with npm. Docker Compose is useful
   for running the complete stack and disposable integration-test dependencies.
3. Install the locked dependencies and Git hooks:

   ```sh
   uv sync --all-packages --locked
   npm ci
   npm run hooks:install
   ```

4. Follow the [Compose guide](docs/compose.md) for a complete local stack, or the
   [development guide](docs/development.md) to run individual services.

Keep `.env`, credentials, tokens, database exports and personal information out of
commits, screenshots and issue reports. Use test accounts and disposable data.

## Find the right place

| Area | Location |
| --- | --- |
| Public reader app | `apps/web` |
| Administration app | `apps/admin` |
| Public, user and admin APIs | `apps/{api,user-api,admin-api}/src` |
| Workers, notifications and CLI | `apps/{aggregator,notifications,cli}/src` |
| Shared backend and HTTP code | `packages/core/src`, `packages/http/src` |
| Shared UI and theme | `packages/ui`, `packages/theme` |
| Database migrations | `migrations/versions` |
| Backend and frontend tests | `tests`, `apps/{web,admin}/tests` |

Read [AGENTS.md](AGENTS.md) for repository conventions. Edit Python files under
`src/`, not generated editable-install links. Regenerate the admin API client
instead of editing its generated files by hand.

## Make a focused change

- Explain the user-visible problem and keep unrelated refactors out of the diff.
- Follow existing components and patterns. Keep keyboard access, mobile layouts,
  loading and error states, and both color themes working.
- Preserve the boundaries between anonymous browsing, user accounts and
  administration. Keep network validation, access controls and abuse protections.
- Add meaningful regression coverage for behavior changes and update the relevant
  documentation. Documentation-only changes generally need link and rendering checks.
- Add a new migration for schema changes; do not rewrite migrations already shipped
  in a release. Describe data compatibility and rollout requirements in the PR.
- Do not bump the application version or create a release tag unless the change is
  explicitly part of release preparation.

## Format and validate

Format Python with Ruff and TypeScript/JavaScript with Prettier:

```sh
npm run format
```

Run the checks relevant to your change:

```sh
# Backend lint, formatting, types and version consistency
bash .github/scripts/python-checks.sh

# Backend unit tests; no dependency services are started
bash scripts/test.sh -q -m 'not integration'

# Reader app
npm run web:lint
npm run web:test
npm run web:build

# Administration app
npm run admin:generate
npm run admin:lint
npm run admin:test
npm run admin:build
```

Run `npm run admin:generate` after backend contract changes and include the updated
schema and generated client. Shared changes may require checking both frontends.
Pre-commit runs formatting and the affected projects' lint, type and unit checks;
it does not replace CI or integration testing. If a formatter changes files,
review and stage those changes before retrying the commit.

Integration tests require explicit `DEVFEED_TEST_DATABASE_URL` and
`DEVFEED_TEST_REDIS_URL`. The PostgreSQL database name must end in `_test`, and
Redis must use database **15**. These tests truncate tables and flush Redis: never
point them at development data you want to keep or a production service. Search
integration tests also require disposable Typesense. See the
[CI guide](docs/ci.md) and [search guide](docs/search.md) for the service setup and
performance checks. A skipped integration test is not a passing verification.

## Open a pull request

Use the [pull-request template](.github/pull_request_template.md). Describe:

- What was wrong or missing, and what now happens.
- How you verified it, including checks you could not run.
- API, configuration or migration changes and any deployment constraints.
- Before/after screenshots for visible UI changes, including mobile and dark mode
  when relevant.

Use clear commit messages such as `Fix source suggestion return URL`. Avoid
committing generated build output or unrelated lockfile changes. Resolve review
conversations and keep the current commit passing `CI required` and CodeQL before
merge. Published runtime images target Linux ARM64; local Compose builds also
support AMD64.

## License and credit

By contributing, you agree that your contributions are provided under the
project's [MIT license](LICENSE). Submit only work you have permission to share,
retain required third-party notices and identify any new dependency licenses.
The project license does not relicense publisher articles, images or other
third-party material displayed by DevFeed.
