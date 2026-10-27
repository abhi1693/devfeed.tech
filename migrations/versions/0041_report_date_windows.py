"""Keep the undeployed 0041 revision available for existing local databases.

The old date-window rewrite changed report tables that 0042 immediately drops.
This bridge makes no schema or data changes. Remove it with the pre-release
0041/0042 migration lineage once local installations have moved to its successor.
"""

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError(
        "Native analytics requires a coordinated backup restore or forward repair; "
        "schema downgrade is not supported"
    )
