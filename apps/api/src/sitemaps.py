"""Crawler inventory is cached independently from high-churn reader responses."""

from typing import Literal

from devfeed_core import sitemaps
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

router = APIRouter(prefix="/v1/sitemaps", tags=["discovery"])


class SitemapPartRef(BaseModel):
    kind: Literal["articles", "topics", "tags", "sources"]
    page: int
    digest: str
    modified_at: float


class SitemapManifest(BaseModel):
    format: int
    generation: str
    built_at: float
    parts: list[SitemapPartRef]
    latest_publication: dict[str, str]


class SitemapEntry(BaseModel):
    path: str
    lastmod: str | None = None


class SitemapPart(BaseModel):
    paths: list[str]
    # Older cached shards only contain paths. Preserve their response shape.
    entries: list[SitemapEntry] | None = None


@router.get("", response_model=SitemapManifest)
def sitemap_index(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    try:
        return sitemaps.manifest()
    except sitemaps.SitemapUnavailable as exc:
        raise HTTPException(
            503,
            "Sitemap is temporarily unavailable",
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        ) from exc


@router.get("/{kind}/{page}", response_model=SitemapPart, response_model_exclude_unset=True)
def sitemap_part(
    response: Response,
    kind: str,
    page: int,
    v: str | None = Query(None, pattern="^[a-f0-9]{32}$"),
):
    if kind not in sitemaps.KINDS or not 1 <= page <= sitemaps.MAX_PARTS:
        raise HTTPException(404, "Sitemap not found")
    try:
        paths = sitemaps.part(kind, page, v)
    except sitemaps.SitemapUnavailable as exc:
        raise HTTPException(
            503,
            "Sitemap is temporarily unavailable",
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        ) from exc
    if paths is None:
        raise HTTPException(404, "Sitemap not found")
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return {"paths": paths} if isinstance(paths, list) else paths
