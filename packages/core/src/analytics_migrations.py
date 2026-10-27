"""Versioned ClickHouse schema changes, recorded only after acknowledged DDL.

The deployment initializer owns upgrades. Runtime services only verify the
installed version; schema history and checksums live entirely in ClickHouse.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable

HISTORY_TABLE = "analytics_schema_migrations"
SCHEMA_LOCK_KEY = 0x44465343


class AnalyticsMigrationError(AnalyticsUnavailable):
    """The installed schema cannot safely follow the packaged migration history."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def statements(self) -> tuple[str, ...]:
        # Explicit delimiters permit semicolons inside quoted SQL and function
        # bodies without implementing a second SQL parser.
        return tuple(
            statement.strip().removesuffix(";").strip()
            for statement in re.split(r"(?m)^-- statement\s*$", self.sql)
        )


@dataclass(frozen=True)
class MigrationStatus:
    current_version: int
    latest_version: int
    pending_versions: tuple[int, ...]


def load_migrations() -> tuple[Migration, ...]:
    directory = files("devfeed_core.clickhouse_migrations")
    migrations = []
    for resource in sorted(directory.iterdir(), key=lambda resource: resource.name):
        if not resource.name.endswith(".sql"):
            continue
        match = re.fullmatch(r"(\d{4})_([a-z][a-z0-9_]*)\.sql", resource.name)
        if match is None:
            raise AnalyticsMigrationError("Invalid ClickHouse migration filename")
        migrations.append(
            Migration(int(match[1]), resource.name, resource.read_text(encoding="utf-8"))
        )
    return tuple(migrations)


class ClickHouseMigrationRunner:
    """Apply an immutable, ordered history under an external deployment lock.

    ClickHouse DDL is not a multi-statement transaction. Each SQL file must be
    safe to replay after a partial failure. A revision is acknowledged only once
    all its statements succeed; an interrupted acknowledgement is reconciled
    against the ClickHouse history on the next invocation.
    """

    def __init__(self, client, *, migrations: tuple[Migration, ...] | None = None):
        self.client = client
        self.migrations = load_migrations() if migrations is None else migrations
        versions = tuple(migration.version for migration in self.migrations)
        if not versions or versions != tuple(range(1, len(versions) + 1)):
            raise AnalyticsMigrationError("ClickHouse migration versions must be contiguous")
        for migration in self.migrations:
            if not migration.name or not all(migration.statements):
                raise AnalyticsMigrationError("ClickHouse migrations must contain SQL statements")

    async def _history(self):
        result = await self.client.query("EXISTS TABLE " + HISTORY_TABLE)
        if (
            len(result) != 1
            or type(result[0].get("result")) is not int
            or result[0]["result"] not in (0, 1)
        ):
            raise AnalyticsMigrationError("Invalid ClickHouse schema history response")
        if not result[0]["result"]:
            return False, {}
        rows = await self.client.query(
            "SELECT version,name,checksum FROM " + HISTORY_TABLE + " ORDER BY version"
        )
        applied = {}
        for row in rows:
            version = row.get("version")
            if type(version) is not int or not 1 <= version <= len(self.migrations):
                raise AnalyticsMigrationError("Unknown ClickHouse migration version")
            migration = self.migrations[version - 1]
            if row.get("name") != migration.name or row.get("checksum") != migration.checksum:
                raise AnalyticsMigrationError(
                    f"ClickHouse migration {version:04d} differs from its applied history"
                )
            applied[version] = migration
        if sorted(applied) != list(range(1, len(applied) + 1)):
            raise AnalyticsMigrationError("ClickHouse schema history contains a version gap")
        return True, applied

    def _status(self, applied) -> MigrationStatus:
        return MigrationStatus(
            current_version=len(applied),
            latest_version=len(self.migrations),
            pending_versions=tuple(
                migration.version
                for migration in self.migrations
                if migration.version not in applied
            ),
        )

    async def current(self) -> MigrationStatus:
        """Inspect history without creating tables or changing application data."""
        _, applied = await self._history()
        return self._status(applied)

    async def check(self) -> MigrationStatus:
        status = await self.current()
        if status.pending_versions:
            raise AnalyticsMigrationError(
                "ClickHouse schema is behind; run 'devfeed analytics upgrade'"
            )
        return status

    async def upgrade(self) -> tuple[int, ...]:
        _, applied = await self._history()
        completed = []
        for migration in self.migrations:
            if migration.version in applied:
                continue
            # Revision 1 creates the journal itself. Its successful DDL is
            # recorded exactly like subsequent revisions, including recovery
            # when the table was created but its acknowledgement was interrupted.
            for statement in migration.statements:
                await self.client.execute(statement)
            await self.client.insert(
                HISTORY_TABLE,
                [
                    {
                        "version": migration.version,
                        "name": migration.name,
                        "checksum": migration.checksum,
                    }
                ],
            )
            completed.append(migration.version)
        return tuple(completed)


async def upgrade_analytics(settings=None) -> MigrationStatus:
    """Serialize deployment upgrades and prepare the historical import boundary.

    A transient PostgreSQL advisory lock prevents concurrent schema writers. It
    stores no analytics, requires no table, and is released when the connection
    closes. The import boundary is fixed before live producers are started.
    """
    from devfeed_core.analytics_import_runner import AnalyticsImportRunner, postgres_connections
    from devfeed_core.config import get_settings

    settings = settings or get_settings()
    connect = postgres_connections(
        settings.database_url, connect_timeout=settings.database_connect_timeout_seconds
    )
    async with connect() as connection:
        row = await (
            await connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (SCHEMA_LOCK_KEY,)
            )
        ).fetchone()
        if not row or row["locked"] is not True:
            raise AnalyticsMigrationError("Another ClickHouse schema upgrade is running")
        async with AnalyticsClient(settings) as client:
            migrations = ClickHouseMigrationRunner(client)
            await migrations.upgrade()
            await AnalyticsImportRunner(
                client,
                connect,
                settings=settings,
                page_size=settings.analytics_backfill_batch_size,
            ).prepare()
            return await migrations.check()
