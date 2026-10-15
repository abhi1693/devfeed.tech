import { resolveReaderRoute, type ReaderRoute } from "../../web/src/lib/reader-routes";
import { publicOrigin } from "./transport";

type ReaderBackground = {
  route: string;
  match: Extract<ReaderRoute, { type: "reader" }>;
};

/** Direct article entries use Latest; a saved background must identify a reader page. */
export function resolveReaderBackground(value: unknown): ReaderBackground | null {
  const route = value == null ? "/latest" : value;
  if (typeof route !== "string" || route === "") return null;
  try {
    const url = new URL(route, publicOrigin);
    if (url.origin !== publicOrigin || url.hash) return null;
    const match = resolveReaderRoute(url.pathname);
    return match?.type === "reader" ? { route, match } : null;
  } catch {
    return null;
  }
}
