"""Normalize historical topic kinds using an operator-mounted JSON map."""

import json
import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from devfeed_core.topic_kind_map import load_topic_kind_map
from devfeed_core.topic_kinds import TOPIC_KINDS

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    topic_rows = connection.execute(sa.text("SELECT id, kind FROM topics")).all()
    proposal_rows = connection.execute(
        sa.text(
            "SELECT id, proposed ->> 'kind' AS proposed_kind, "
            "baseline #>> '{draft,kind}' AS baseline_kind, "
            "applied #>> '{draft,kind}' AS applied_kind FROM topic_proposals"
        )
    ).all()
    canonical = set(TOPIC_KINDS)
    legacy_values = {value for _, value in topic_rows if value not in canonical} | {
        value
        for row in proposal_rows
        for value in row[1:]
        if value is not None and value not in canonical
    }

    map_path = os.environ.get("DEVFEED_TOPIC_KIND_MAP_PATH")
    kind_map = load_topic_kind_map(Path(map_path)) if map_path else {}
    unmapped = sorted(legacy_values - kind_map.keys())
    if unmapped:
        raise RuntimeError(
            "Historical topic kinds require a mounted mapping at "
            "DEVFEED_TOPIC_KIND_MAP_PATH; unmapped values: " + ", ".join(unmapped)
        )

    for identifier, value in topic_rows:
        if value not in canonical:
            connection.execute(
                sa.text("UPDATE topics SET kind = :kind WHERE id = :id"),
                {"kind": kind_map[value], "id": identifier},
            )
    for identifier, *values in proposal_rows:
        for column, path, value in zip(
            ("proposed", "baseline", "applied"),
            ("{kind}", "{draft,kind}", "{draft,kind}"),
            values,
            strict=True,
        ):
            if value is not None and value not in canonical:
                connection.execute(
                    sa.text(
                        f"UPDATE topic_proposals SET {column} = jsonb_set("
                        f"{column}, '{path}', CAST(:kind_json AS jsonb), false) WHERE id = :id"
                    ),
                    {"kind_json": json.dumps(kind_map[value]), "id": identifier},
                )

    op.create_check_constraint(
        "ck_topic_kind",
        "topics",
        "kind IN (" + ", ".join(f"'{kind}'" for kind in TOPIC_KINDS) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_topic_kind", "topics", type_="check")
