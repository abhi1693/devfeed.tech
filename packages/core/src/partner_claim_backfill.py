"""One-time migration of live legacy receipts to operational delivery claims.

Old receipt writers must be drained before this import begins. The caller must
checkpoint each page in ClickHouse only after this transaction commits. A replay
must not replace a newer claim.
"""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from psycopg import sql

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Legacy claims are needed only until
# every pre-cutover signed receipt has expired and the verified import is done.
# Signed receipt timestamps are non-negative Unix seconds, so year 1 cannot
# collide with a real, currently valid receipt observation.
LEGACY_CLAIM_AT = datetime(1, 1, 1, tzinfo=UTC)
MAX_CLAIMS = 1000


async def backfill_partner_claims(connection, records):
    """Idempotently transfer at most one frozen legacy event page to claims."""
    if not isinstance(records, (list, tuple)) or len(records) > MAX_CLAIMS:
        raise ValueError("Invalid legacy partner receipt page")
    grouped: dict[tuple[uuid.UUID, uuid.UUID], list[Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "asset_id",
            "delivery_id",
            "kind",
            "day",
        }:
            raise ValueError("Invalid legacy partner receipt")
        try:
            asset = uuid.UUID(str(record["asset_id"]))
            delivery = uuid.UUID(str(record["delivery_id"]))
            day = record["day"]
            if isinstance(day, str):
                day = date.fromisoformat(day)
            if not isinstance(day, date) or isinstance(day, datetime):
                raise ValueError
            kind = record["kind"]
            if kind not in {"impression", "click"}:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid legacy partner receipt") from error
        item = grouped.setdefault((asset, delivery), [day, None, None])
        if item[0] != day:
            raise ValueError("Inconsistent legacy partner receipt day")
        item[1 if kind == "impression" else 2] = LEGACY_CLAIM_AT
    if not grouped:
        return
    placeholders = sql.SQL(",").join([sql.SQL("(%s,%s,%s,%s,%s)") for _ in grouped])
    statement = sql.SQL(
        "INSERT INTO partner_placement_deliveries "
        "(asset_id,delivery_id,issued_at,impression_recorded_at,click_recorded_at) "
        "VALUES {} ON CONFLICT (delivery_id) DO UPDATE SET "
        "impression_recorded_at=COALESCE(partner_placement_deliveries.impression_recorded_at,"
        "EXCLUDED.impression_recorded_at),"
        "click_recorded_at=COALESCE(partner_placement_deliveries.click_recorded_at,"
        "EXCLUDED.click_recorded_at) "
        "WHERE partner_placement_deliveries.asset_id=EXCLUDED.asset_id "
        "RETURNING delivery_id"
    ).format(placeholders)
    params: list[Any] = []
    for (asset, delivery), (day, impression, click) in grouped.items():
        params.extend((asset, delivery, datetime.combine(day, time.min, UTC), impression, click))
    async with connection.transaction():
        rows = await (await connection.execute(statement, params)).fetchall()
        if len(rows) != len(grouped):
            raise RuntimeError("Conflicting legacy partner receipt owner")
