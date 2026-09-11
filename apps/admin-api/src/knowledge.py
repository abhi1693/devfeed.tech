"""Read-only, bounded projections of the catalog. No inferred or duplicated edges."""

import uuid
from typing import Annotated, Literal

from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    TopicRelation,
    TopicRelationProposal,
    UserAccount,
    UserInterest,
    UserRecommendation,
    UserRecommendationState,
    UserTopic,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ORMModel
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import (
    String,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    union_all,
)
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.search import text_search, topic_search

router = APIRouter(
    prefix="/v1/admin/knowledge",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_admin)],
)
NodeKind = Literal["topic", "article", "tag", "source", "user"]
Layer = Literal["article", "tag", "source", "user"]
Relation = Literal["uses_language", "depends_on", "implements", "part_of", "related_to"]
NodeId = Annotated[
    str,
    Field(pattern=r"^(topic|article|tag|source|user):[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$"),
]
EDGE_BUDGET = 1500
PATH_EDGE_BUDGET = 5000
PATH_NODE_BUDGET = 2000


def read_snapshot(session: DB):
    # A worker or administrator can change links during traversal. All nodes,
    # edges and counts in a response must describe the same database snapshot.
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    return session


ReadDB = Annotated[Session, Depends(read_snapshot)]


class GraphNode(ORMModel):
    id: str
    entity_id: uuid.UUID
    kind: NodeKind
    label: str
    description: str | None
    status: str | None
    subtype: str | None


class GraphEdge(ORMModel):
    id: str
    source: str
    target: str
    kind: str
    directed: bool
    status: Literal["approved", "pending", "saved"]
    role: str | None
    origin: str | None
    evidence: str | None
    evidence_url: str | None
    relevance: float | None


class GraphOut(ORMModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    catalog_counts: dict[str, int]
    truncated: bool
    node_limit: int
    edge_limit: int


class GraphSearchOut(ORMModel):
    items: list[GraphNode]
    total: int


class GraphPathOut(ORMModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    found: bool
    truncated: bool
    max_hops: int


def bound_query_time(session: Session):
    # Limit database work as well as response size; scoped to this read transaction.
    session.execute(select(func.set_config("statement_timeout", "5000", True)))


def node_key(kind, identifier):
    return literal(kind + ":") + cast(identifier, String)


class Projection:
    """One filter definition shared by search, expansion, and path traversal."""

    def __init__(self, layers, include_pending=False, relation=None, published_only=False):
        self.kinds = {"topic", *layers}
        self.topics = select(Topic.id).where(Topic.status == "active")
        self.articles = select(Article.id)
        if published_only:
            self.articles = self.articles.where(Article.publication_status == "published")
        self.include_pending = include_pending
        self.relation = relation

    def nodes(self, q: str = ""):
        def row(model, kind, label, description, status=None, subtype=None):
            return select(
                node_key(kind, model.id).label("id"),
                model.id.label("entity_id"),
                literal(kind).label("kind"),
                label.label("label"),
                func.left(description, 400).label("description"),
                (status if status is not None else cast(literal(None), String)).label("status"),
                (subtype if subtype is not None else cast(literal(None), String)).label("subtype"),
            )

        rows = [
            row(
                Topic,
                "topic",
                Topic.name,
                func.coalesce(Topic.description, Topic.ai_description),
                Topic.status,
                Topic.kind,
            ).where(Topic.id.in_(self.topics))
        ]
        if q:
            rows[0] = rows[0].where(topic_search(q))
        if "article" in self.kinds:
            statement = row(
                Article,
                "article",
                Article.title,
                Article.summary,
                Article.publication_status,
                Article.review_status,
            ).where(Article.id.in_(self.articles))
            rows.append(statement.where(text_search(q, Article.title)) if q else statement)
        if "tag" in self.kinds:
            statement = row(Tag, "tag", Tag.name, cast(literal(None), String))
            rows.append(
                statement.where(
                    text_search(q, Tag.name, Tag.slug, func.array_to_string(Tag.aliases, " "))
                )
                if q
                else statement
            )
        if "source" in self.kinds:
            statement = row(
                Source,
                "source",
                Source.name,
                Source.website_url,
                Source.approval_status,
                Source.source_type,
            )
            rows.append(
                statement.where(text_search(q, Source.name, Source.website_url)) if q else statement
            )
        if "user" in self.kinds:
            label = func.coalesce(
                UserAccount.profile["display_name"].astext,
                UserAccount.name,
                literal("User ") + cast(UserAccount.id, String),
            )
            statement = row(UserAccount, "user", label, literal("Personalized feed account"))
            rows.append(
                statement.where(text_search(q, label, cast(UserAccount.id, String)))
                if q
                else statement
            )
        return union_all(*rows).subquery("graph_nodes")

    def edges(self):
        def row(
            model,
            source,
            target,
            kind,
            *,
            suffix=None,
            status="saved",
            role=None,
            origin=None,
            evidence=None,
            url=None,
            relevance=None,
        ):
            edge_kind = literal(kind) if isinstance(kind, str) else kind

            def nullable(value):
                return value if value is not None else cast(literal(None), String)

            return select(
                (
                    source
                    + literal("/")
                    + edge_kind
                    + literal("/")
                    + target
                    + (literal("/") + cast(suffix, String) if suffix is not None else literal(""))
                ).label("id"),
                source.label("source"),
                target.label("target"),
                edge_kind.label("kind"),
                (edge_kind != "related_to").label("directed"),
                literal(status).label("status"),
                nullable(role).label("role"),
                nullable(origin).label("origin"),
                func.left(nullable(evidence), 1000).label("evidence"),
                nullable(url).label("evidence_url"),
                (
                    relevance
                    if relevance is not None
                    else cast(literal(None), ArticleTopic.relevance.type)
                ).label("relevance"),
            ).select_from(model)

        def topic_edges(model, **kwargs):
            statement = row(
                model,
                node_key("topic", model.topic_id),
                node_key("topic", model.related_topic_id),
                model.relation,
                **kwargs,
            ).where(
                model.topic_id.in_(self.topics),
                model.related_topic_id.in_(self.topics),
            )
            return statement.where(model.relation == self.relation) if self.relation else statement

        rows = [topic_edges(TopicRelation, status="approved", url=TopicRelation.evidence_url)]
        if self.include_pending:
            proposal = TopicRelationProposal
            # An approved edge takes precedence even if an older proposal remains pending.
            approved = exists().where(
                TopicRelation.relation == proposal.relation,
                or_(
                    (TopicRelation.topic_id == proposal.topic_id)
                    & (TopicRelation.related_topic_id == proposal.related_topic_id),
                    (proposal.relation == "related_to")
                    & (TopicRelation.topic_id == proposal.related_topic_id)
                    & (TopicRelation.related_topic_id == proposal.topic_id),
                ),
            )
            rows.append(
                topic_edges(
                    proposal,
                    suffix=proposal.id,
                    status="pending",
                    origin=literal("ai"),
                    evidence=proposal.explanation + literal("\n\n") + proposal.evidence_quote,
                    url=proposal.evidence_url,
                ).where(proposal.status == "pending", ~approved)
            )
        if "article" in self.kinds:
            rows.append(
                row(
                    ArticleTopic,
                    node_key("article", ArticleTopic.article_id),
                    node_key("topic", ArticleTopic.topic_id),
                    "classified_as",
                    role=ArticleTopic.role,
                    origin=ArticleTopic.origin,
                    evidence=ArticleTopic.evidence,
                    relevance=ArticleTopic.relevance,
                ).where(
                    ArticleTopic.topic_id.in_(self.topics),
                    ArticleTopic.article_id.in_(self.articles),
                )
            )
        if "tag" in self.kinds:
            rows.append(
                row(
                    Tag,
                    node_key("tag", Tag.id),
                    node_key("topic", Tag.topic_id),
                    "mapped_to",
                ).where(Tag.topic_id.in_(self.topics))
            )
            if "article" in self.kinds:
                rows.append(
                    row(
                        ArticleTag,
                        node_key("article", ArticleTag.article_id),
                        node_key("tag", ArticleTag.tag_id),
                        "tagged_with",
                        origin=ArticleTag.origin,
                    ).where(ArticleTag.article_id.in_(self.articles))
                )
        if "source" in self.kinds and "article" in self.kinds:
            # Multiple feed entries from the same source are one topology edge.
            rows.append(
                row(
                    ArticleOrigin,
                    node_key("source", ArticleOrigin.source_id),
                    node_key("article", ArticleOrigin.article_id),
                    "provided",
                )
                .where(ArticleOrigin.article_id.in_(self.articles))
                .distinct()
            )
        if "user" in self.kinds:
            rows.append(
                row(
                    UserTopic,
                    node_key("user", UserTopic.user_id),
                    node_key("topic", UserTopic.topic_id),
                    "follows",
                ).where(UserTopic.topic_id.in_(self.topics))
            )

            def current(model):
                return exists(
                    select(UserRecommendationState.user_id).where(
                        UserRecommendationState.user_id == model.user_id,
                        UserRecommendationState.invalidated.is_(False),
                        UserRecommendationState.expires_at > utcnow(),
                    )
                )

            rows.append(
                row(
                    UserInterest,
                    node_key("user", UserInterest.user_id),
                    node_key("topic", UserInterest.topic_id),
                    "interested_in",
                    role=UserInterest.reason,
                    origin=literal("recommendation"),
                    relevance=UserInterest.weight / 100.0,
                ).where(
                    UserInterest.topic_id.in_(self.topics),
                    UserInterest.reason != "followed_topic",
                    current(UserInterest),
                )
            )
            if "article" in self.kinds:
                rows.append(
                    row(
                        ArticleLike,
                        node_key("user", ArticleLike.user_id),
                        node_key("article", ArticleLike.article_id),
                        "likes",
                    ).where(ArticleLike.article_id.in_(self.articles))
                )
                rows.append(
                    row(
                        UserRecommendation,
                        node_key("user", UserRecommendation.user_id),
                        node_key("article", UserRecommendation.article_id),
                        "recommended",
                        role=UserRecommendation.reason,
                        origin=literal("recommendation"),
                        relevance=UserRecommendation.score,
                    ).where(
                        UserRecommendation.article_id.in_(self.articles),
                        UserRecommendation.article_id.in_(
                            select(Article.id).where(visible_article())
                        ),
                        current(UserRecommendation),
                    )
                )
        return union_all(*rows).subquery("graph_edges")


def load_nodes(session: Session, projection: Projection, identifiers):
    if not identifiers:
        return []
    rows = projection.nodes()
    return [
        GraphNode.model_validate(row)
        for row in session.execute(
            select(rows)
            .where(rows.c.id.in_(identifiers))
            .order_by(rows.c.kind, func.lower(rows.c.label), rows.c.id)
        ).mappings()
    ]


def validate_nodes(session, projection, identifiers):
    if len(load_nodes(session, projection, identifiers)) != len(set(identifiers)):
        raise HTTPException(
            404,
            "An object is unavailable in the selected layers. Topics must be active.",
        )


def edge_rows(session, edges, predicate=None, limit=EDGE_BUDGET):
    statement = select(edges)
    if predicate is not None:
        statement = statement.where(predicate)
    # Approved topic relationships come first, then context, then suggestions.
    statement = statement.order_by(
        case((edges.c.status == "approved", 0), (edges.c.status == "saved", 1), else_=2),
        case(
            (
                edges.c.kind.in_(["classified_as", "tagged_with", "provided", "mapped_to"]),
                1,
            ),
            else_=0,
        ),
        edges.c.id,
    ).limit(limit + 1)
    rows = [GraphEdge.model_validate(row) for row in session.execute(statement).mappings()]
    return rows[:limit], len(rows) > limit


def unique_edges(rows):
    """related_to is symmetric, even for reverse links made through legacy CRUD."""
    result: dict[object, GraphEdge] = {}
    for edge in rows:
        key = (
            (tuple(sorted((edge.source, edge.target))), edge.kind, edge.status)
            if not edge.directed
            else edge.id
        )
        result.setdefault(key, edge)
    return list(result.values())


@router.get("/search", response_model=GraphSearchOut, operation_id="admin_knowledge_search")
def search(
    session: ReadDB,
    q: str = Query("", max_length=200),
    layers: list[Layer] = Query(default=[], max_length=4),
    published_only: bool = False,
):
    bound_query_time(session)
    rows = Projection(layers, published_only=published_only).nodes(q.strip())
    total = session.scalar(select(func.count()).select_from(rows)) or 0
    return GraphSearchOut(
        total=total,
        items=[
            GraphNode.model_validate(row)
            for row in session.execute(
                select(rows)
                .order_by(
                    case((rows.c.kind == "topic", 0), else_=1),
                    func.lower(rows.c.label),
                    rows.c.id,
                )
                .limit(25)
            ).mappings()
        ],
    )


@router.get("/graph", response_model=GraphOut, operation_id="admin_knowledge_graph")
def graph(
    session: ReadDB,
    focus: NodeId | None = None,
    expand: list[NodeId] = Query(default=[], max_length=20),
    layers: list[Layer] = Query(default=[], max_length=4),
    depth: int = Query(1, ge=1, le=2),
    limit: int = Query(150, ge=25, le=300),
    relation: Relation | None = None,
    include_pending: bool = False,
    published_only: bool = False,
):
    bound_query_time(session)
    projection = Projection(layers, include_pending, relation, published_only)
    nodes = projection.nodes()
    edges = projection.edges()
    counts = {
        kind: count
        for kind, count in session.execute(
            select(nodes.c.kind, func.count()).group_by(nodes.c.kind)
        )
    }
    chosen: set[str] = set()
    selected: dict[str, GraphEdge] = {}
    truncated = False

    def add(rows):
        nonlocal truncated
        for edge in rows:
            extra = {edge.source, edge.target} - chosen
            if len(chosen) + len(extra) > limit or len(selected) >= EDGE_BUDGET:
                truncated = True
                continue
            chosen.update(extra)
            selected[edge.id] = edge

    roots = set(expand) | ({focus} if focus else set())
    validate_nodes(session, projection, roots)
    chosen.update(roots)
    if focus:
        frontier = {focus}
        for _ in range(depth):
            rows, clipped = edge_rows(
                session,
                edges,
                or_(edges.c.source.in_(frontier), edges.c.target.in_(frontier)),
            )
            before = chosen.copy()
            add(rows)
            truncated |= clipped
            frontier = chosen - before
            if not frontier:
                break
    else:
        rows, clipped = edge_rows(session, edges)
        add(rows)
        truncated |= clipped
        # Isolated topics remain discoverable, including a freshly populated catalog.
        remaining = limit - len(chosen)
        if remaining:
            chosen.update(
                session.scalars(
                    select(nodes.c.id)
                    .where(~nodes.c.id.in_(chosen))
                    .order_by(
                        case((nodes.c.kind == "topic", 0), else_=1),
                        func.lower(nodes.c.label),
                        nodes.c.id,
                    )
                    .limit(remaining)
                )
            )
        truncated |= sum(counts.values()) > len(chosen)
    if expand:
        rows, clipped = edge_rows(
            session, edges, or_(edges.c.source.in_(expand), edges.c.target.in_(expand))
        )
        add(rows)
        truncated |= clipped
    # Include cross-links among visible neighbours, not just the traversal tree.
    rows, clipped = edge_rows(
        session, edges, edges.c.source.in_(chosen) & edges.c.target.in_(chosen)
    )
    add(rows)
    truncated |= clipped
    return GraphOut(
        nodes=load_nodes(session, projection, chosen),
        edges=unique_edges(selected.values()),
        catalog_counts=counts,
        truncated=truncated,
        node_limit=limit,
        edge_limit=EDGE_BUDGET,
    )


@router.get("/path", response_model=GraphPathOut, operation_id="admin_knowledge_path")
def path(
    session: ReadDB,
    from_node: NodeId,
    to_node: NodeId,
    layers: list[Layer] = Query(default=[], max_length=4),
    max_hops: int = Query(4, ge=1, le=6),
    direction: Literal["any", "outgoing"] = "any",
    relation: Relation | None = None,
    include_pending: bool = False,
    published_only: bool = False,
):
    bound_query_time(session)
    projection = Projection(layers, include_pending, relation, published_only)
    validate_nodes(session, projection, {from_node, to_node})
    edges = projection.edges()
    frontier = {from_node}
    parents: dict[str, tuple[str, GraphEdge] | None] = {from_node: None}
    remaining = PATH_EDGE_BUDGET
    truncated = False
    for _ in range(max_hops):
        if to_node in parents or not frontier:
            break
        predicate = or_(
            edges.c.source.in_(frontier),
            edges.c.target.in_(frontier)
            & (edges.c.directed.is_(False) if direction == "outgoing" else literal(True)),
        )
        rows, clipped = edge_rows(session, edges, predicate, remaining)
        truncated |= clipped
        remaining -= len(rows)
        following = set()
        for edge in rows:
            for source, target in [
                (edge.source, edge.target),
                (edge.target, edge.source),
            ]:
                if source not in frontier or target in parents:
                    continue
                if direction == "outgoing" and edge.directed and source != edge.source:
                    continue
                if len(parents) >= PATH_NODE_BUDGET:
                    truncated = True
                    break
                parents[target] = (source, edge)
                following.add(target)
        frontier = following
        if not remaining or len(parents) >= PATH_NODE_BUDGET:
            truncated = True
            break
    found = to_node in parents
    route: list[GraphEdge] = []
    identifiers = {from_node, to_node}
    if found:
        current = to_node
        while (parent := parents[current]) is not None:
            previous, edge = parent
            route.append(edge)
            identifiers.add(previous)
            current = previous
        route.reverse()
    return GraphPathOut(
        nodes=load_nodes(session, projection, identifiers),
        edges=route,
        found=found,
        truncated=truncated,
        max_hops=max_hops,
    )
