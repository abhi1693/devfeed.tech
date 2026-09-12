# Repository Guidelines

## Project Structure & Module Organization

DevFeed is a uv/npm monorepo. Python services live in `apps/{api,admin-api,user-api,aggregator,notifications,cli}/src/`;
`apps/web` and `apps/admin` contain the Next.js reader and administration interfaces. Shared backend logic lives in
`packages/core` and `packages/http`; shared components, styles, and assets live in `packages/ui` and `packages/theme`.
Alembic migrations belong in `migrations/`, backend tests in `tests/`, and frontend tests in each app's `tests/`. See
`docs/development.md` for architecture and setup.

## Build, Test, and Development Commands

Use Python 3.12+, uv, and Node.js 22.13+.

- `uv sync --all-packages --locked` and `npm ci`: install locked dependencies.
- `npm run hooks:install`: enable scoped pre-commit checks.
- `uv run devfeed db upgrade`: migrate the local database.
- `uv run uvicorn devfeed_api.main:app --reload --no-proxy-headers`: start the public API.
- `uv run devfeed worker` and `uv run devfeed scheduler`: start background processing.
- `npm run web:dev` / `npm run admin:dev`: start reader/admin interfaces on ports 3000/3001.
- `npm run web:build` / `npm run admin:build`: build production frontends.
- `bash .github/scripts/python-checks.sh`: check Python lint, formatting, types, and versions.
- `npm run web:lint` / `npm run admin:lint`: check frontend formatting, ESLint, and TypeScript.

## Coding Style & Naming Conventions

Use four-space Python indentation and two-space TypeScript/JavaScript indentation, with a 100-column formatting target.
Run `npm run format` for Ruff and Prettier formatting. Follow existing snake_case Python modules, kebab-case frontend
filenames, and PascalCase React components. Edit Python `src/` files, not generated editable-install links. After admin
API contract changes, run `npm run admin:generate` and include generated updates.

## Testing Guidelines

Run `uv run pytest` for backend tests and `npm run web:test` / `npm run admin:test` for Vitest suites. Name tests
`test_*.py` or `*.test.ts(x)`. Add regression coverage for changed behavior; no numeric coverage threshold is
configured. Integration tests require disposable `DEVFEED_TEST_DATABASE_URL` and `DEVFEED_TEST_REDIS_URL`: the database
name must end in `_test`, and Redis must use database 15. Fixtures truncate tables and flush Redis.

## Commit & Pull Request Guidelines

Use focused, imperative commit subjects, such as `Hide empty sources from reader discovery`. Follow
`.github/pull_request_template.md`: explain the problem and resulting behavior, link applicable issues, report
validation gaps, include UI screenshots, and document migrations or rollout risks. Update relevant documentation and
tests. Before merging, pass `CI required` and CodeQL checks and resolve review conversations.

## Security & Configuration

Copy `.env.example`; never commit secrets. Set database and Redis URLs explicitly. See `docs/compose.md` for the local
stack. Preserve public, user, and admin service boundaries and feed-fetch SSRF protections.
