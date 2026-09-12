"""Public article projection shared by discovery and personalized feeds.

Keep private editorial state and source polling/history fields out of these reads.
Raiseload makes a serializer expansion fail visibly instead of creating an N+1.
"""

from sqlalchemy.orm import load_only, selectinload

from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Tag, Topic

PUBLIC_ARTICLE_OPTIONS = (
    load_only(
        Article.id,
        Article.canonical_url,
        Article.title,
        Article.slug,
        Article.summary,
        Article.ai_summary,
        Article.ai_description,
        Article.published_to_feed_at,
        Article.metadata_source_type,
        Article.author,
        Article.image_url,
        Article.language,
        Article.content_type,
        Article.content_format,
        Article.published_at,
        Article.feed_at,
        Article.discovered_at,
        raiseload=True,
    ),
    selectinload(Article.tags).load_only(Tag.slug, raiseload=True),
    selectinload(Article.origins)
    .load_only(
        ArticleOrigin.source_id,
        ArticleOrigin.original_url,
        ArticleOrigin.source_metadata,
        raiseload=True,
    )
    .joinedload(ArticleOrigin.source)
    .load_only(
        Source.id,
        Source.approval_status,
        Source.name,
        Source.slug,
        Source.source_type,
        Source.description,
        Source.website_url,
        Source.logo_url,
        Source.image_url,
        Source.language,
        raiseload=True,
    ),
    selectinload(Article.topic_links)
    .load_only(
        ArticleTopic.topic_id,
        ArticleTopic.role,
        ArticleTopic.relevance,
        raiseload=True,
    )
    .joinedload(ArticleTopic.topic)
    .load_only(
        Topic.id,
        Topic.name,
        Topic.slug,
        Topic.kind,
        Topic.status,
        raiseload=True,
    ),
)
