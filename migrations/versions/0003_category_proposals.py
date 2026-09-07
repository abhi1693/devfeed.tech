"""Add category descriptions and a separate supervised proposal inbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_category_proposals"
down_revision = "0002_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("categories", sa.Column("description", sa.String(500), nullable=True))
    op.create_table(
        "category_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="SET NULL")),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("proposed", postgresql.JSONB(), nullable=False),
        sa.Column("baseline", postgresql.JSONB()),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", postgresql.JSONB()),
        sa.Column("review_note", sa.String(1000)),
        sa.Column("applied", postgresql.JSONB()),
        sa.CheckConstraint("status IN ('pending','approved','rejected')"),
        sa.CheckConstraint("action IN ('create','update')"),
        sa.CheckConstraint("origin IN ('import','article_enrichment')"),
        sa.CheckConstraint(
            "status = 'pending' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)"
        ),
        sa.CheckConstraint("status <> 'approved' OR applied IS NOT NULL"),
    )
    for field in ("batch_id", "category_id", "status"):
        op.create_index(f"ix_category_proposals_{field}", "category_proposals", [field])
    for label, field in (("slug", "slug"), ("target", "category_id")):
        op.create_index(
            f"uq_category_proposal_pending_{label}",
            "category_proposals",
            [field],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        )


def downgrade():
    op.drop_table("category_proposals")
    op.drop_column("categories", "description")
