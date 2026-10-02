"""Separate canonical products from launch platform listings and URL identities."""

import hashlib
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def identity_url(value):
    # Frozen migration normalization: preserve paths, query order and functional parameters.
    parts = urlsplit(value)
    host = parts.hostname.encode("idna").decode().lower().rstrip(".")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port and (parts.scheme, parts.port) not in {("https", 443), ("http", 80)}:
        netloc += f":{parts.port}"
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    query = parts.query if len(kept) == len(pairs) else urlencode(kept)
    return urlunsplit((parts.scheme, netloc, parts.path or "/", query, ""))


def upgrade():
    op.add_column(
        "partner_products",
        sa.Column("merged_into_id", sa.Uuid(), sa.ForeignKey("partner_products.id")),
    )
    op.create_table(
        "partner_listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("partner_products.id"), nullable=False),
        sa.Column(
            "provider",
            sa.String(200),
            sa.ForeignKey("partner_connections.provider"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("listing_url", sa.String(2048), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("attribution", sa.String(300), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("identity_status", sa.String(20), nullable=False),
        sa.Column("identity_reason", sa.Text()),
        sa.Column("seen_generation", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_partner_listing_identity"),
        sa.CheckConstraint(
            "identity_status IN ('resolved', 'unresolved')",
            name="ck_partner_listing_identity_status",
        ),
    )
    op.create_index("ix_partner_listings_product_id", "partner_listings", ["product_id"])
    op.add_column("partner_products", sa.Column("metadata_listing_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_partner_metadata_listing",
        "partner_products",
        "partner_listings",
        ["metadata_listing_id"],
        ["id"],
    )
    op.create_table(
        "partner_product_urls",
        sa.Column("url_hash", sa.String(64), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("partner_products.id"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_partner_product_urls_product_id", "partner_product_urls", ["product_id"])
    bind = op.get_bind()
    meta = sa.MetaData()
    products = sa.Table("partner_products", meta, autoload_with=bind)
    listings = sa.Table("partner_listings", meta, autoload_with=bind)
    urls = sa.Table("partner_product_urls", meta, autoload_with=bind)
    identities = {}
    for row in (
        bind.execute(
            sa.select(products)
            .where(products.c.connection_id.is_not(None))
            .order_by(products.c.updated_at, products.c.id)
        )
        .mappings()
        .all()
    ):
        url = identity_url(row["product_url"])
        key = hashlib.sha256(url.encode()).hexdigest()
        owner = identities.get(key)
        identifier = owner["id"] if owner else row["id"]
        listing_id = uuid.uuid4()
        bind.execute(
            listings.insert().values(
                id=listing_id,
                product_id=identifier,
                provider=row["connection_id"],
                external_id=row["external_id"],
                name=row["name"],
                product_url=row["product_url"],
                listing_url=row["listing_url"],
                description=row["description"],
                pricing=row["pricing"],
                attribution=row["attribution"],
                active=row["status"] != "withdrawn",
                identity_status="resolved",
                seen_generation=row["seen_generation"],
                updated_at=row["updated_at"],
            )
        )
        if owner:
            owner["excluded"] = owner["excluded"] or row["excluded"]
            owner["reviews"] += row["reviews"]
            bind.execute(
                products.update()
                .where(products.c.id == row["id"])
                .values(
                    merged_into_id=identifier,
                    status="withdrawn",
                    verified_at=None,
                    revision=row["revision"] + 1,
                )
            )
            bind.execute(
                products.update()
                .where(products.c.id == identifier)
                .values(
                    excluded=owner["excluded"],
                    reviews=owner["reviews"],
                    status="paused" if owner["excluded"] else "pending",
                )
            )
        else:
            identities[key] = {
                "id": identifier,
                "excluded": row["excluded"],
                "reviews": list(row["reviews"]),
            }
            bind.execute(urls.insert().values(url_hash=key, url=url, product_id=identifier))
            bind.execute(
                products.update()
                .where(products.c.id == identifier)
                .values(
                    metadata_listing_id=listing_id,
                    product_url=url,
                    assessment_revision=0,
                    assessment={},
                    verified_at=None,
                    revision=row["revision"] + 1,
                    status="paused"
                    if row["excluded"]
                    else "withdrawn"
                    if row["status"] == "withdrawn"
                    else "pending",
                )
            )
    bind.execute(
        sa.text(
            "UPDATE partner_products p SET status='withdrawn' "
            "WHERE p.metadata_listing_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM partner_listings l WHERE l.product_id=p.id AND l.active=true)"
        )
    )
    # Old snapshots and queued work refer to the former per-provider product identities.
    for name in ("partner_pipeline_jobs", "partner_evaluations"):
        bind.execute(
            sa.text(
                f"UPDATE {name} SET status='failed', "
                "error='Product identity migration superseded this job', finished_at=now(), "
                "lease_token=NULL, lease_until=NULL WHERE status IN ('queued','running')"
            )
        )
    bind.execute(sa.text("UPDATE partner_connections SET next_sync_at=now() WHERE enabled=true"))
    op.drop_constraint("uq_partner_identity", "partner_products", type_="unique")
    for name in (
        "provider",
        "external_id",
        "listing_url",
        "attribution",
        "connection_id",
        "seen_generation",
    ):
        op.drop_column("partner_products", name)


def downgrade():
    raise RuntimeError(
        "Product identities have been consolidated. "
        "Restore a database backup to return to schema 0022."
    )
