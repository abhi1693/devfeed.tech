import { contentTypeFromRoute } from "./feed-query";

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const segment = (value: string) =>
  value.length > 0 &&
  value.length <= 200 &&
  !/[\x00-\x20/\\?#]/.test(value) &&
  ![".", ".."].includes(value);

export type AiRoute =
  | { kind: "article"; slug: string }
  | { kind: "directory"; collection: "topics" | "sources" | "tags" }
  | { kind: "search" }
  | { kind: "feed"; collection?: "topics" | "sources" | "tags"; id?: string; contentType?: string };

/** Explicit public allowlist, shared by negotiation and the directly reachable renderer. */
export function aiRoute(pathname: string): AiRoute | undefined {
  let parts: string[];
  try {
    parts = pathname.replace(/\/$/, "").split("/").slice(1).map(decodeURIComponent);
  } catch {
    return undefined;
  }
  if (pathname === "/" || pathname === "/index") return { kind: "feed" };
  if (parts.some((part) => !segment(part))) return undefined;
  const [first, id, type] = parts;
  if (parts.length === 1) {
    if (first === "search") return { kind: "search" };
    if (first === "topics" || first === "sources" || first === "tags")
      return { kind: "directory", collection: first };
    const contentType = contentTypeFromRoute(first);
    if (contentType) return { kind: "feed", contentType };
  }
  if (first === "articles" && parts.length === 2) return { kind: "article", slug: id };
  if (
    (first === "topics" || first === "sources" || first === "tags") &&
    (parts.length === 2 || parts.length === 3)
  ) {
    if ((first === "sources" && !uuid.test(id)) || (first !== "sources" && id.length > 100))
      return undefined;
    const contentType = type ? contentTypeFromRoute(type) : undefined;
    if (type && !contentType) return undefined;
    return { kind: "feed", collection: first, id, contentType };
  }
  return undefined;
}
