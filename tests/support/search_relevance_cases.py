"""Curated reader-search relevance cases for the developer-news catalogue."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RelevanceCase:
    query: str
    expected_relevance: tuple[tuple[str, int], ...]

    @property
    def expected_ids(self):
        return tuple(identifier for identifier, _ in self.expected_relevance)


DOCUMENTS = [
    {
        "id": "article-kubernetes-networking",
        "title": "Kubernetes networking with CNI and service routing",
        "description": "How Kubernetes networking, CNI plugins, and service routing work.",
        "terms": ["kubernetes", "k8s", "networking", "cni", "service routing"],
        "author": "DevFeed Editorial",
        "published_at": 6,
    },
    {
        "id": "article-kubernetes-deployments",
        "title": "Safer Kubernetes deployments with progressive delivery",
        "description": "Deployment patterns for Kubernetes applications and workloads.",
        "terms": ["kubernetes", "k8s", "deployment", "rollout", "workloads"],
        "author": "DevFeed Editorial",
        "published_at": 5,
    },
    {
        "id": "article-python-async",
        "title": "Practical Python async programming",
        "description": "Asyncio, concurrency, and reliable Python service patterns.",
        "terms": ["python", "async", "asyncio", "concurrency"],
        "author": "DevFeed Editorial",
        "published_at": 4,
    },
    {
        "id": "article-postgres-pgbouncer",
        "title": "PostgreSQL connection pooling with PgBouncer",
        "description": "Pool sizing, transaction boundaries, and PostgreSQL operations.",
        "terms": ["postgres", "postgresql", "pgbouncer", "database", "pooling"],
        "author": "DevFeed Editorial",
        "published_at": 3,
    },
    {
        "id": "article-typesense-aliases",
        "title": "Zero-downtime Typesense reindexing with collection aliases",
        "description": "Build, validate, and switch Typesense collections with aliases.",
        "terms": ["typesense", "search", "aliases", "reindex", "zero downtime"],
        "author": "DevFeed Editorial",
        "published_at": 2,
    },
    {
        "id": "article-rss-ingestion",
        "title": "Reliable RSS ingestion for developer news",
        "description": "Feed polling, deduplication, and durable ingestion workflows.",
        "terms": ["rss", "feeds", "ingestion", "developer news", "deduplication"],
        "author": "DevFeed Editorial",
        "published_at": 1,
    },
    {
        "id": "article-networking-news",
        "title": "Latest network reliability techniques",
        "description": "New approaches to networking and service reliability.",
        "terms": ["networking", "network reliability", "services"],
        "author": "DevFeed Editorial",
        "published_at": 99,
    },
    {
        "id": "article-kubernetes-reference",
        "title": "Kubernetes",
        "description": "A reference guide to the Kubernetes platform.",
        "terms": ["k8s", "platform", "orchestration"],
        "author": "DevFeed Editorial",
        "published_at": 0,
    },
]


CASES = (
    RelevanceCase(
        "kubernetes networking",
        (("article-kubernetes-networking", 3), ("article-kubernetes-deployments", 1)),
    ),
    RelevanceCase(
        "kubernetes",
        (("article-kubernetes-reference", 3), ("article-kubernetes-networking", 2)),
    ),
    RelevanceCase(
        "kuberentes networking",
        (("article-kubernetes-networking", 3),),
    ),
    RelevanceCase("k8s cni", (("article-kubernetes-networking", 3),)),
    RelevanceCase("python async", (("article-python-async", 3),)),
    RelevanceCase("postgres pgbouncer", (("article-postgres-pgbouncer", 3),)),
    RelevanceCase("typesense aliases", (("article-typesense-aliases", 3),)),
    RelevanceCase("rss ingestion", (("article-rss-ingestion", 3),)),
)
