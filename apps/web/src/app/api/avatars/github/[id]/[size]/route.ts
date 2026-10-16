import { createHash } from "node:crypto";
import { cardImage } from "@/lib/server/card-image";
import { githubAvatarWidths } from "@/lib/avatar";

export const runtime = "nodejs";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string; size: string }> },
) {
  const { id, size } = await params;
  const query = new URL(request.url).searchParams;
  const version = query.get("v");
  if (
    !/^\d{1,15}$/.test(id) ||
    !githubAvatarWidths.some((width) => String(width) === size) ||
    [...query].some(([key, value]) => key !== "v" || !/^\d{1,10}$/.test(value)) ||
    query.getAll("v").length > 1
  ) {
    return new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  // The host/path are constructed here. Callers cannot choose arbitrary origins,
  // credentials, transformations or redirect targets; the shared fetcher pins public DNS.
  const upstream = `https://avatars.githubusercontent.com/u/${id}?s=${size}${version ? `&v=${version}` : ""}`;
  const image = await cardImage(upstream, "avatar", 5000, Number(size));
  if (!image) return new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });
  const body = Buffer.from(image.slice("data:image/webp;base64,".length), "base64");
  const etag = `"${createHash("sha256").update(body).digest("hex")}"`;
  const headers = {
    "Content-Type": "image/webp",
    "Cache-Control": "public, max-age=3600",
    ETag: etag,
    "X-Content-Type-Options": "nosniff",
  };
  if (
    request.headers
      .get("If-None-Match")
      ?.split(",")
      .map((value) => value.trim())
      .some((value) => value === "*" || value.replace(/^W\//, "") === etag)
  ) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(body, { headers });
}
