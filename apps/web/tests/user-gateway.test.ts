import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { gateway } from "@/lib/server/gateway";

beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://user.example");
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("forwards only user cookies and preserves callback cookie rotation", async () => {
  const upstream = new Response(null, {
    status: 302,
    headers: { Location: "https://user.example/my-feed" },
  });
  upstream.headers.append(
    "Set-Cookie",
    "__Host-devfeed_user_session=opaque; Secure; HttpOnly; Path=/",
  );
  upstream.headers.append(
    "Set-Cookie",
    "__Host-devfeed_user_state=; Max-Age=0; Secure; HttpOnly; Path=/",
  );
  const fetcher = vi.fn().mockResolvedValue(upstream);
  vi.stubGlobal("fetch", fetcher);
  const result = await gateway(
    new Request("https://user.example/api/v1/user/auth/callback?state=s&code=c", {
      headers: {
        Cookie:
          "__Host-devfeed_admin_session=admin-secret; __Host-devfeed_user_state=browser; unrelated=private",
        Authorization: "Bearer untrusted",
        "X-Forwarded-Host": "evil.example",
      },
    }),
    ["v1", "user", "auth", "callback"],
  );
  expect(result.status).toBe(302);
  expect(result.headers.getSetCookie()).toHaveLength(2);
  expect(result.headers.get("cache-control")).toBe("no-store");
  expect(result.headers.get("referrer-policy")).toBe("no-referrer");
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe("http://user-api:8002/v1/user/auth/callback?state=s&code=c");
  expect(options.redirect).toBe("manual");
  expect(options.headers.get("cookie")).toBe("__Host-devfeed_user_state=browser");
  expect(options.headers.get("authorization")).toBeNull();
  expect(options.headers.get("x-forwarded-host")).toBeNull();
});

it.each([
  ["v1", "admin", "auth", "me"],
  ["v1", "feed"],
  ["v1", "user", "..", "auth"],
  ["v1", "user", "%2e%2e", "auth"],
])("rejects unrelated or traversal path %j", async (...path) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await gateway(new Request("https://user.example/api/test"), path)).status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([null, "https://admin.example", "https://evil.example"])(
  "rejects mutation origin %s",
  async (origin) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect(
      (
        await gateway(
          new Request("https://user.example/api/v1/user/preferences", {
            method: "PUT",
            headers: origin ? { Origin: origin } : {},
          }),
          ["v1", "user", "preferences"],
        )
      ).status,
    ).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("forwards bounded preference writes and CSRF", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ topic_ids: [] }));
  vi.stubGlobal("fetch", fetcher);
  const request = new Request("https://user.example/api/v1/user/preferences", {
    method: "PUT",
    headers: {
      Origin: "https://user.example",
      "X-CSRF-Token": "csrf",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ topic_ids: [] }),
  });
  expect((await gateway(request, ["v1", "user", "preferences"])).status).toBe(200);
  expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
  expect(new TextDecoder().decode(fetcher.mock.calls[0][1].body)).toBe('{"topic_ids":[]}');
});
it("bounds request bodies before contacting the service", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const request = new Request("https://user.example/api/v1/user/preferences", {
    method: "PUT",
    headers: { Origin: "https://user.example" },
    body: "x".repeat(1_000_001),
  });
  expect((await gateway(request, ["v1", "user", "preferences"])).status).toBe(413);
  expect(fetcher).not.toHaveBeenCalled();
});

it("forwards anonymous article clicks and round-trips only the visitor cookie", async () => {
  const visitor = "__Host-devfeed_user_visitor=opaque";
  const upstream = Response.json({ article_id: "article", opens: 1, likes: 0, liked: false });
  upstream.headers.append("Set-Cookie", `${visitor}; Secure; HttpOnly; SameSite=lax; Path=/`);
  const fetcher = vi.fn().mockResolvedValue(upstream);
  vi.stubGlobal("fetch", fetcher);
  const response = await gateway(
    new Request("https://user.example/api/v1/user/articles/article/open", {
      method: "POST",
      headers: { Origin: "https://user.example", Cookie: `${visitor}; unrelated=private` },
    }),
    ["v1", "user", "articles", "article", "open"],
  );
  expect(response.status).toBe(200);
  expect(response.headers.getSetCookie()).toEqual([
    `${visitor}; Secure; HttpOnly; SameSite=lax; Path=/`,
  ]);
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe("http://user-api:8002/v1/user/articles/article/open");
  expect(options.headers.get("cookie")).toBe(visitor);
  expect(options.headers.get("x-csrf-token")).toBeNull();
  expect(options.headers.get("origin")).toBe("https://user.example");
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("preserves the anonymous tracking throttle and retry delay", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: "Too many article opens. Please try again later." },
          { status: 429, headers: { "Retry-After": "45" } },
        ),
      ),
  );
  const response = await gateway(
    new Request("https://user.example/api/v1/user/articles/article/open", {
      method: "POST",
      headers: { Origin: "https://user.example" },
    }),
    ["v1", "user", "articles", "article", "open"],
  );
  expect(response.status).toBe(429);
  expect(response.headers.get("retry-after")).toBe("45");
  expect(response.headers.get("cache-control")).toBe("no-store");
});
it("fails privately without configuration or a reachable user API", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "");
  const result = await gateway(new Request("https://user.example/api/v1/user/auth/me"), [
    "v1",
    "user",
    "auth",
    "me",
  ]);
  expect(result.status).toBe(503);
  expect(await result.json()).toEqual({ detail: "User service unavailable" });
  expect(result.headers.get("cache-control")).toBe("no-store");
});
