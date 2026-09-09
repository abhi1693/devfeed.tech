# Database migrations

`versions/0001_initial_schema.py` is the frozen initial schema for DevFeed 0.0.1.
It was generated from the baseline models, not by concatenating historical upgrade
steps. It creates the original 18 tables, constraints and indexes directly. No
accounts, login/session tables, seed taxonomy or legacy data conversions remain.

`0002_notifications` adds the audience-scoped notification delivery outbox.
Existing databases on `0001_initial` only need `uv run devfeed db upgrade`; no
reset is needed. Chimely owns a separate database and its own migrations.

`0007_admin_preferences` adds account-scoped UI preferences without altering content
or login sessions. Existing accounts receive defaults until they save settings.

## One-time pre-release reset

The previous nine-revision development chain was discarded before the first
commit. Databases stamped with that chain cannot use this baseline in place.
Back up anything needed, stop API/scheduler/workers, and reset/recreate the
development database yourself. This repository does not run that reset for you.
Do not simply `stamp head`: it bypasses the actual schema operations.

With the empty database configured through the existing `DEVFEED_DATABASE_URL`
and `DEVFEED_REDIS_URL`:

```sh
uv run devfeed db upgrade
uv run devfeed db check
uv run devfeed cache clear
```

Cache clearing only invalidates DevFeed GET responses; it does not flush Redis or
delete RQ jobs. Old queued jobs may refer to database records that no longer exist;
review those separately with your processes stopped. Never flush shared Redis as
a shortcut. Re-add source feeds after the reset and start services yourself.

`/health/ready` checks the required revision from `devfeed_core.version`. It will
report `migration_required` for an old revision until you complete the reset and
upgrade. `/version` reports the application's **required** schema revision, not
the current database revision; `devfeed db current` reports the latter.

## Future schema changes

Never regenerate or edit the baseline again once it is committed/applied. Each
new schema change gets a separate Alembic revision linked to the previous head:

```sh
uv run alembic revision --autogenerate -m "describe the schema change"
```

Use a development database already at the current migration head. Review generated
operations, imports, constraints, indexes and any destructive effects. Set
`SCHEMA_REVISION` in `packages/core/src/version.py` to the new revision when the
application requires it. New files include the installed app version in their
docstring and `app_version` annotation. Keep that annotation unchanged later;
changing the app version alone does not create a database revision.

When ready, explicitly run `uv run devfeed db upgrade`, then `uv run devfeed db check`.
`uv run alembic history` shows the chain. `uv run alembic upgrade head --sql` renders
SQL without executing it. Runtime code never imports current models inside an
existing revision; each revision is a self-contained schema snapshot.

Generated migration files have no dedicated test cases. Application integration
tests provision their disposable schema through Alembic before testing behavior.
Baseline downgrade drops all its tables/data; it must never be run casually.
