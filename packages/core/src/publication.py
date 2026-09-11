"""Canonical eligibility for public and personalized article discovery."""

from sqlalchemy import and_

from devfeed_core.models import Article, ArticleOrigin, Source


def visible_article():
    return and_(
        Article.publication_status == "published",
        Article.review_status == "approved",
        Article.origins.any(ArticleOrigin.source.has(Source.approval_status == "approved")),
    )
