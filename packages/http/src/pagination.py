"""Bounded, deterministic service lists. All filtering happens in SQL."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Annotated, TypedDict

from devfeed_core.models import Base
from devfeed_core.schemas import MAX_OFFSET, TEXT_INPUT_PATTERN
from devfeed_core.services import OperationConflict, RecordNotFound
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select, SQLColumnExpression


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageData[T](TypedDict):
    items: list[T]
    total: int
    limit: int
    offset: int


class ListQuery:
    def __init__(
        self,
        q: str = Query("", max_length=200, pattern=TEXT_INPUT_PATTERN),
        sort: str | None = Query(None, max_length=50),
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0, le=MAX_OFFSET),
    ) -> None:
        self.q, self.sort, self.limit, self.offset = q.strip(), sort, limit, offset


Listing = Annotated[ListQuery, Depends()]


def paginate[T: Base](
    session: Session,
    statement: Select[T],
    query: ListQuery,
    sorting: Mapping[str, SQLColumnExpression[object]],
    default: str = "name",
) -> PageData[T]:
    order = query.sort or default
    column = sorting.get(order.removeprefix("-"))
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    model = statement.column_descriptions[0]["entity"]
    rows = list(
        session.scalars(
            statement.order_by(
                column.desc() if order.startswith("-") else column.asc(),
                *model.__mapper__.primary_key,
            )
            .offset(query.offset)
            .limit(query.limit)
        ).all()
    )
    return {"items": rows, "total": total or 0, "limit": query.limit, "offset": query.offset}


def record[T: Base](
    session: Session, model: type[T], identifier: uuid.UUID, *, lock: bool = False
) -> T:
    statement = select(model).where(model.__table__.c.id == identifier)
    if lock:
        statement = statement.with_for_update(of=model)
    value = session.scalar(statement)
    if value is None:
        raise RecordNotFound(f"{model.__name__} not found")
    return value


def require_record[T: Base](session: Session, model: type[T], identifier: uuid.UUID) -> None:
    """Validate a nested resource's parent without hydrating it or its relationships."""
    if (
        session.scalar(select(model.__table__.c.id).where(model.__table__.c.id == identifier))
        is None
    ):
        raise RecordNotFound(f"{model.__name__} not found")


def prohibit_references(
    session: Session,
    references: Iterable[tuple[str, Select[object]]],
) -> None:
    for label, statement in references:
        if session.scalar(select(statement.exists())):
            raise OperationConflict(
                f"Cannot delete: linked {label} exist. Remove those links first."
            )
