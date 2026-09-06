from devfeed_core.models import Article, Source, Topic
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(prefix="/v1/admin", tags=["admin-overview"])


class AdminOverview(BaseModel):
    articles: int
    articles_pending_review: int
    articles_published: int
    sources: int
    sources_pending_review: int
    topics: int


@router.get("/overview", response_model=AdminOverview, operation_id="admin_overview")
def overview(admin: Admin, session: DB):
    article = session.execute(
        select(
            func.count(Article.id),
            func.count(Article.id).filter(Article.review_status == "pending"),
            func.count(Article.id).filter(Article.publication_status == "published"),
        )
    ).one()
    source = session.execute(
        select(
            func.count(Source.id),
            func.count(Source.id).filter(Source.approval_status == "pending"),
        )
    ).one()
    return AdminOverview(
        articles=article[0],
        articles_pending_review=article[1],
        articles_published=article[2],
        sources=source[0],
        sources_pending_review=source[1],
        topics=session.scalar(select(func.count()).select_from(Topic)) or 0,
    )
