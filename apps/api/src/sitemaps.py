"""Crawler inventory is cached independently from high-churn reader responses."""

from devfeed_core import sitemaps
from fastapi import APIRouter, HTTPException, Query, Response

router = APIRouter(prefix="/v1/sitemaps", tags=["discovery"])


@router.get("")
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


@router.get("/{kind}/{page}")
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
