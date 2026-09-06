"""Bounded, deterministic administration lists. All filtering happens in SQL."""

from typing import Annotated

from devfeed_core.services import OperationConflict, RecordNotFound
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ListQuery:
    def __init__(
        self,
        q: str = Query("", max_length=200),
        sort: str | None = Query(None, max_length=50),
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        self.q, self.sort, self.limit, self.offset = q.strip(), sort, limit, offset


Listing = Annotated[ListQuery, Depends()]


def paginate(session, statement, query, sorting, default="name"):
    order = query.sort or default
    column = sorting.get(order.removeprefix("-"))
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    model = statement.column_descriptions[0]["entity"]
    rows = session.scalars(
        statement.order_by(
            column.desc() if order.startswith("-") else column.asc(), *model.__mapper__.primary_key
        )
        .offset(query.offset)
        .limit(query.limit)
    ).all()
    return {"items": rows, "total": total, "limit": query.limit, "offset": query.offset}


def record(session, model, identifier, *, lock=False):
    statement = select(model).where(model.id == identifier)
    if lock:
        statement = statement.with_for_update(of=model)
    value = session.scalar(statement)
    if value is None:
        raise RecordNotFound(f"{model.__name__} not found")
    return value


def prohibit_references(session, references):
    for label, statement in references:
        if session.scalar(select(statement.exists())):
            raise OperationConflict(
                f"Cannot delete: linked {label} exist. Remove those links first."
            )
