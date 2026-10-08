"""Persist configurable partner API definitions and migrate Nick Launches."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

NICK_CONNECTOR = {
    "version": 1,
    "base_url": "https://nicklaunches.com",
    "list_path": "/api/v1/products/",
    "detail_path": "/api/v1/products/{id}/",
    "items_paths": ["items", "results"],
    "detail_root": "",
    "fields": {
        "external_id": ["slug"],
        "name": ["name"],
        "product_url": ["url", "productUrl"],
        "listing_url": ["productUrl", "url"],
        "description": ["description", "tagline"],
        "pricing": ["pricing"],
    },
    "pagination": {
        "mode": "cursor",
        "parameter": "cursor",
        "next_path": "nextCursor",
        "size_parameter": "limit",
        "page_size": 10,
        "start": 1,
        "total_path": None,
        "has_more_path": None,
    },
    "auth": {"mode": "none", "secret_ref": "", "header": "X-API-Key"},
    "filters": [{"path": "categories", "values": ["Developer Tools"], "include_missing": True}],
    "parameters": {},
    "platform_hosts": ["nicklaunches.com", "www.nicklaunches.com"],
    "listing_url_template": None,
    "attribution": "Via Nick Launches",
    "requests_per_minute": 60,
    "timeout_seconds": 15,
    "max_response_bytes": 2000000,
    "max_pages": 1000,
}


def upgrade():
    op.add_column(
        "partner_connections", sa.Column("name", sa.String(200), nullable=False, server_default="")
    )
    op.add_column(
        "partner_connections",
        sa.Column("connector", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    table = sa.table(
        "partner_connections",
        sa.column("provider", sa.String()),
        sa.column("name", sa.String()),
        sa.column("connector", postgresql.JSONB()),
    )
    op.get_bind().execute(
        table.update()
        .where(table.c.provider == "nick-launches")
        .values(name="Nick Launches", connector=NICK_CONNECTOR)
    )
    # Snapshot old queued/running generations too, without resetting their progress.
    jobs = sa.table(
        "partner_pipeline_jobs",
        sa.column("provider", sa.String()),
        sa.column("operation", sa.String()),
        sa.column("payload", postgresql.JSONB()),
    )
    op.get_bind().execute(
        jobs.update()
        .where(jobs.c.provider == "nick-launches", jobs.c.operation == "sync")
        .values(
            payload=jobs.c.payload.op("||")(
                sa.cast({"connector": NICK_CONNECTOR}, postgresql.JSONB())
            )
        )
    )


def downgrade():
    op.drop_column("partner_connections", "connector")
    op.drop_column("partner_connections", "name")
