# App versions and release annotations

DevFeed uses one `MAJOR.MINOR.PATCH` version for its Python and npm workspace projects.
The root `pyproject.toml` is the source of truth; workspace manifests and both lockfiles
carry synchronized package versions so wheels and runtime metadata agree. API
route version `/v1`, app release version, AI prompt version and Alembic revision
are distinct identifiers.

The initial version is `0.0.1` and is not tagged/released yet. `CHANGELOG.md`
records what belongs to each release. There are no automatic Git commits, tags,
pushes, service restarts, package publications or database operations.

## Inspect versions

```sh
uv run devfeed --version
uv run devfeed-worker --version
uv run devfeed-scheduler --version
uv run python scripts/version.py check
```

The API exposes `GET /version`, OpenAPI `info.version`, and `X-DevFeed-Version`
headers. CLI/API/worker client identity use installed `devfeed-core` metadata;
they do not read a possibly missing repository file or use an environment override.
`--version` works without database/Redis configuration and starts no services.

## Prepare the next release

```sh
uv run python scripts/version.py bump patch --dry-run
uv run python scripts/version.py bump patch
# Or select an explicit greater release version:
uv run python scripts/version.py set 0.2.0
uv sync --all-packages --locked
uv run python scripts/version.py check
```

Choose one bump/set operation, not both. Minor/major bumps are supported too.
Versions must have three nonnegative numeric components with no leading zeroes;
prerelease/build suffixes are not currently supported by this helper. It rejects
existing workspace/lockfile/generated-version drift, updates all Python/npm manifests
together, runs an offline `uv lock` and `uv sync --all-packages --locked --offline`,
then runs `npm run admin:generate`. Syncing first ensures the OpenAPI exporter sees
the new installed version. Both the admin schema and every generated client header
are checked against the release version. Install npm dependencies with `npm ci`
before bumping; Python dependencies must already be cached by `uv sync`.
If locking, syncing or generation fails, its version edits, lockfiles and generated
artifacts are rolled back. Run `uv sync --all-packages --locked` after a failed bump
to restore installed package metadata too. The helper does not change
dependency constraints, historical migration annotations or the changelog.

Add a dated release entry to `CHANGELOG.md`, record notable changes, and review
the version and lockfile diff. Run checks and the application tests; verify the
installed version again after syncing. Long-running API/workers need a deliberate
reload to report a newly installed version. Use the same version for image tags,
for example `docker build -t devfeed:0.0.1 .`; building does not start services.

## Annotated Git versions

Only after the release commit is explicitly approved and created, add an annotated
tag pointing to that reviewed commit, for example:

```sh
git tag -a v0.0.1 -m "DevFeed 0.0.1"
git show v0.0.1
```

Push the commit/tag only with separate approval. Never move a released tag or
rewrite a published version; prepare a new patch release instead. This workflow
does not currently create hosted releases or publish container/package artifacts.

Migration files record the app version at generation time, but new app releases
do not require new migration files unless the schema changes. See the
[migration workflow](../migrations/README.md).
