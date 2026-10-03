import "server-only";
import { NextResponse, type NextRequest } from "next/server";
import { publicSiteOrigin, xPixelEnabled } from "./config";

export function captureXClick<T extends Response>(request: NextRequest, response: T): T {
  if (!xPixelEnabled() || request.method !== "GET") return response;
  const value = request.nextUrl.searchParams.get("twclid");
  if (!value || value.length > 512 || /[^A-Za-z0-9_-]/.test(value)) return response;
  const secure = new URL(publicSiteOrigin()).protocol === "https:";
  const name = secure ? "__Host-devfeed_user_x_click" : "devfeed_user_x_click";
  if (request.cookies.get(name)?.value === value) return response;
  const cookieResponse = response instanceof NextResponse ? response : new NextResponse();
  cookieResponse.cookies.set(name, value, {
    httpOnly: true,
    secure,
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 86400,
  });
  if (cookieResponse !== response)
    response.headers.append("Set-Cookie", cookieResponse.headers.get("Set-Cookie")!);
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}
