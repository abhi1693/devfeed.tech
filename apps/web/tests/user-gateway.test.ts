import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as me from "@/app/api/v1/user/auth/me/route";
import * as login from "@/app/api/v1/user/auth/login/route";
import * as callback from "@/app/api/v1/user/auth/callback/route";
import * as logout from "@/app/api/v1/user/auth/logout/route";
import * as preferences from "@/app/api/v1/user/preferences/route";
import * as profile from "@/app/api/v1/user/settings/profile/route";
import * as avatar from "@/app/api/v1/user/settings/profile/avatar/route";
import * as feed from "@/app/api/v1/user/feed/route";
import * as articleOpen from "@/app/api/v1/user/articles/[article_id]/open/route";
import * as inboxStream from "@/app/api/v1/user/notifications/chimely/v1/inbox/stream/route";
import * as notificationRead from "@/app/api/v1/user/notifications/chimely/v1/inbox/notifications/[notification_id]/read/route";
import * as broadcastRead from "@/app/api/v1/user/notifications/chimely/v1/inbox/broadcasts/[broadcast_id]/read/route";
import * as placements from "@/app/api/v1/user/partner-placements/query/route";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));

const origin = "https://user.example";
const api = "http://user-api:8002";
const articleId = "11111111-1111-4111-8111-111111111111";
const sessionCookies =
  "devfeed_user_session=one=two; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor";
const allCookies = `${sessionCookies}; __Host-devfeed_user_x_click=click-1; devfeed_user_x_click=click-2; _twclid=pixel-cookie; prefix_devfeed_user_session=bad; devfeed_user_session_extra=bad; __Host-devfeed_admin_session=private; devfeed_partner_session=private; other=private`;
type Params = Record<string, string | string[]>;
type CookieCase = {
  name: string;
  handler: typeof me.GET;
  method: string;
  params: Params;
};
const context = (params: Params = {}) =>
  Object.keys(params).length ? { params: Promise.resolve(params) } : {};

beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  vi.stubEnv("DEVFEED_USER_API_URL", api);
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", "[]");
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "false");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("bound reader cookie and origin policies", () => {
  it.each<CookieCase>([
    { name: "identity", handler: me.GET, method: "GET", params: {} },
    { name: "callback", handler: callback.GET, method: "GET", params: {} },
    { name: "profile", handler: profile.GET, method: "GET", params: {} },
    { name: "feed", handler: feed.GET, method: "GET", params: {} },
    { name: "preferences", handler: preferences.PUT, method: "PUT", params: {} },
    { name: "avatar", handler: avatar.POST, method: "POST", params: {} },
    { name: "logout", handler: logout.POST, method: "POST", params: {} },
    {
      name: "anonymous article open",
      handler: articleOpen.POST,
      method: "POST",
      params: { article_id: articleId },
    },
    { name: "inbox", handler: inboxStream.GET, method: "GET", params: {} },
  ])(
    "forwards only complete reader session-family cookies for $name",
    async ({ handler, method, params }) => {
      vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
      const fetcher = vi.fn().mockResolvedValue(Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      expect(
        (
          await handler(
            new Request(`${origin}/api`, {
              method,
              headers: {
                Origin: origin,
                Cookie: allCookies,
                Authorization: "private",
                "X-CSRF-Token": "csrf",
              },
              ...(!["GET", "HEAD"].includes(method) ? { body: "{}" } : {}),
            }),
            context(params),
          )
        ).status,
      ).toBe(200);
      const headers = fetcher.mock.calls[0][1].headers as Headers;
      expect(headers.get("cookie")).toBe(sessionCookies);
      expect(headers.get("authorization")).toBeNull();
      expect(headers.get("x-csrf-token")).toBe("csrf");
    },
  );

  it.each([undefined, "false", "true"])(
    "adds attribution only to the login operation while its flag is %s",
    async (flag) => {
      vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", flag);
      const fetcher = vi.fn().mockImplementation(() => Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      await login.GET(
        new Request(`${origin}/api/v1/user/feed`, { headers: { Cookie: allCookies } }),
        {},
      );
      const loginCookies = fetcher.mock.calls[0][1].headers.get("cookie");
      expect(loginCookies).toBe(
        flag === "true"
          ? `${sessionCookies}; __Host-devfeed_user_x_click=click-1; devfeed_user_x_click=click-2; _twclid=pixel-cookie`
          : sessionCookies,
      );
      await me.GET(
        new Request(`${origin}/api/v1/user/auth/login`, { headers: { Cookie: allCookies } }),
        {},
      );
      expect(fetcher.mock.calls[1][1].headers.get("cookie")).toBe(sessionCookies);
      expect(fetcher.mock.calls[0][0]).toBe(`${api}/v1/user/auth/login`);
      expect(fetcher.mock.calls[1][0]).toBe(`${api}/v1/user/auth/me`);
    },
  );

  it.each([null, "https://admin.example", "https://evil.example"])(
    "rejects mutation origin %s before consuming a body",
    async (suppliedOrigin) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method: "PUT",
        headers: suppliedOrigin ? { Origin: suppliedOrigin } : {},
        body: new ReadableStream<Uint8Array>(),
        duplex: "half",
      } as RequestInit);
      expect((await preferences.PUT(incoming, {})).status).toBe(403);
      expect(incoming.bodyUsed).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("preserves an explicitly trusted extension origin, user cookie and CSRF", async () => {
    const id = "a".repeat(32);
    vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", JSON.stringify([id]));
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const response = await logout.POST(
      new Request(`${origin}/api`, {
        method: "POST",
        headers: {
          Origin: `chrome-extension://${id}`,
          "X-CSRF-Token": "csrf-token",
          Cookie: "__Host-devfeed_user_session=session; admin_session=private",
        },
      }),
      {},
    );
    expect(response.status).toBe(204);
    const headers = fetcher.mock.calls[0][1].headers as Headers;
    expect(headers.get("origin")).toBe(`chrome-extension://${id}`);
    expect(headers.get("x-csrf-token")).toBe("csrf-token");
    expect(headers.get("cookie")).toBe("__Host-devfeed_user_session=session");
  });

  it.each([
    `chrome-extension://${"b".repeat(32)}`,
    `chrome-extension://${"a".repeat(32)}/`,
    "null",
  ])("rejects an untrusted extension-like origin: %s", async (suppliedOrigin) => {
    vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", JSON.stringify(["a".repeat(32)]));
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect(
      (
        await logout.POST(
          new Request(`${origin}/api`, {
            method: "POST",
            headers: { Origin: suppliedOrigin, "X-CSRF-Token": "valid-token" },
          }),
          {},
        )
      ).status,
    ).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(['["*"]', "{invalid"])(
    "fails closed for malformed extension-origin configuration: %s",
    async (ids) => {
      vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", ids);
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      expect(
        (
          await logout.POST(
            new Request(`${origin}/api`, {
              method: "POST",
              headers: { Origin: `chrome-extension://${"a".repeat(32)}` },
            }),
            {},
          )
        ).status,
      ).toBe(503);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each(['["*"]', "{invalid"])(
    "does not consult mutation-only extension configuration for safe reads: %s",
    async (ids) => {
      vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", ids);
      const fetcher = vi.fn().mockResolvedValue(Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      expect((await me.GET(new Request(`${origin}/api`), {})).status).toBe(200);
      expect(fetcher).toHaveBeenCalledOnce();
    },
  );
});

describe("reader transport and concrete parameters", () => {
  it("rejects a mismatched method before resolving route parameters or consuming a body", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const incoming = new Request(`${origin}/api`, {
      method: "POST",
      headers: { Origin: origin },
      body: new ReadableStream<Uint8Array>(),
      duplex: "half",
    } as RequestInit);
    const params = vi.fn(() => {
      throw new Error("A rejected method must not resolve route parameters");
    });
    const routeContext = Object.defineProperty({}, "params", { get: params });
    const response = await me.GET(incoming, routeContext);
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("GET");
    expect(await response.json()).toEqual({ detail: "Method not allowed" });
    expect(params).not.toHaveBeenCalled();
    expect(incoming.bodyUsed).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects the generated identity HEAD handler with its documented method", async () => {
    const response = me.HEAD();
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("GET");
    expect(await response.json()).toEqual({ detail: "Method not allowed" });
  });

  it.each([{}, { params: undefined }])(
    "handles real parameterless Next contexts: %j",
    async (routeContext) => {
      const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
      vi.stubGlobal("fetch", fetcher);
      expect((await me.GET(new Request(`${origin}/api`), routeContext)).status).toBe(204);
      expect(fetcher.mock.calls[0][1]).toMatchObject({
        method: "GET",
        body: undefined,
        cache: "no-store",
      });
      expect(fetcher.mock.calls[0][1].headers.get("accept")).toBe("application/json");
    },
  );

  it.each([
    {},
    { article_id: "article" },
    { article_id: "../auth" },
    { article_id: "%2e%2e" },
    { article_id: [articleId] },
  ] as Params[])(
    "rejects missing or unsafe article parameters before reading the body: %j",
    async (params) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { Origin: origin },
        body: new ReadableStream<Uint8Array>(),
        duplex: "half",
      } as RequestInit);
      const response = await articleOpen.POST(incoming, context(params));
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ detail: "Not found" });
      expect(incoming.bodyUsed).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("preserves conditional, stream, correlation and retry headers while stripping private headers", async () => {
    const allowed = {
      "content-type": "text/event-stream",
      "x-request-id": "request-id",
      "x-devfeed-version": "1.2.3",
      etag: '"revision"',
      "x-accel-buffering": "no",
      "retry-after": "30",
    };
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(null, { status: 304, headers: { ...allowed, "x-private": "secret" } }),
      );
    vi.stubGlobal("fetch", fetcher);
    const response = await inboxStream.GET(
      new Request(`${origin}/api`, {
        headers: {
          "if-none-match": '"revision"',
          "last-event-id": "event-42",
          "x-private": "secret",
          "x-chimely-subscriber-hash": "forged",
        },
      }),
      {},
    );
    expect(response.status).toBe(304);
    for (const [key, value] of Object.entries(allowed))
      expect(response.headers.get(key)).toBe(value);
    expect(response.headers.get("x-private")).toBeNull();
    const headers = fetcher.mock.calls[0][1].headers as Headers;
    expect(headers.get("if-none-match")).toBe('"revision"');
    expect(headers.get("last-event-id")).toBe("event-42");
    expect(headers.get("x-private")).toBeNull();
    expect(headers.get("x-chimely-subscriber-hash")).toBeNull();
  });

  it("preserves callback redirects and independent session/state cookie rotation", async () => {
    const upstream = new Response(null, { status: 302, headers: { Location: `${origin}/` } });
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
    const response = await callback.GET(
      new Request(`${origin}/api?state=s&code=c`, {
        headers: {
          Cookie:
            "__Host-devfeed_admin_session=secret; __Host-devfeed_user_state=browser; unrelated=private",
          Authorization: "Bearer untrusted",
          "X-Forwarded-Host": "evil.example",
        },
      }),
      {},
    );
    expect(response.status).toBe(302);
    expect(response.headers.getSetCookie()).toEqual(upstream.headers.getSetCookie());
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe(`${api}/v1/user/auth/callback?state=s&code=c`);
    expect(options.redirect).toBe("manual");
    expect(options.headers.get("cookie")).toBe("__Host-devfeed_user_state=browser");
    expect(options.headers.get("authorization")).toBeNull();
    expect(options.headers.get("x-forwarded-host")).toBeNull();
  });

  it("round-trips the anonymous visitor cookie and article-open throttling", async () => {
    const visitor = "__Host-devfeed_user_visitor=opaque";
    const upstream = Response.json({ article_id: articleId, opens: 1, likes: 0, liked: false });
    upstream.headers.append("Set-Cookie", `${visitor}; Secure; HttpOnly; SameSite=lax; Path=/`);
    const fetcher = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetcher);
    const incoming = () =>
      new Request(`${origin}/api`, {
        method: "POST",
        headers: { Origin: origin, Cookie: `${visitor}; unrelated=private` },
      });
    const response = await articleOpen.POST(incoming(), context({ article_id: articleId }));
    expect(response.status).toBe(200);
    expect(response.headers.getSetCookie()).toEqual(upstream.headers.getSetCookie());
    expect(fetcher.mock.calls[0][0]).toBe(`${api}/v1/user/articles/${articleId}/open`);
    expect(fetcher.mock.calls[0][1].headers.get("cookie")).toBe(visitor);
    expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBeNull();
    fetcher.mockResolvedValue(
      Response.json(
        { detail: "Too many article opens." },
        { status: 429, headers: { "Retry-After": "45" } },
      ),
    );
    const limited = await articleOpen.POST(incoming(), context({ article_id: articleId }));
    expect(limited.status).toBe(429);
    expect(limited.headers.get("retry-after")).toBe("45");
    expect(limited.headers.get("cache-control")).toBe("no-store");
  });

  it("streams inbox data without waiting for the upstream body to finish", async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
        ),
    );
    const response = await inboxStream.GET(new Request(`${origin}/api`), {});
    const reader = response.body!.getReader();
    stream.enqueue(new TextEncoder().encode("data: hello\n\n"));
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: hello\n\n");
    stream.close();
    expect((await reader.read()).done).toBe(true);
    reader.releaseLock();
  });

  it.each([
    { handler: notificationRead.POST, name: "notification_id", prefix: "notif_", other: "bcast_" },
    { handler: broadcastRead.POST, name: "broadcast_id", prefix: "bcast_", other: "notif_" },
  ])(
    "binds $name only to its declared inbox action before reading a body",
    async ({ handler, name, prefix, other }) => {
      const fetcher = vi.fn().mockResolvedValue(Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      for (const value of [
        undefined,
        "bad-id",
        prefix + "a".repeat(25),
        prefix + "A".repeat(26),
        other + "a".repeat(26),
      ]) {
        const request = new Request(`${origin}/api`, {
          method: "POST",
          headers: { Origin: origin },
          body: new ReadableStream<Uint8Array>(),
          duplex: "half",
        } as RequestInit);
        expect(
          (await handler(request, context(value === undefined ? {} : { [name]: value }))).status,
        ).toBe(404);
        expect(request.bodyUsed).toBe(false);
        expect(fetcher).not.toHaveBeenCalled();
      }
      const id = prefix + "a".repeat(26);
      expect(
        (
          await handler(
            new Request(`${origin}/api`, {
              method: "POST",
              headers: { Origin: origin },
              body: "{}",
            }),
            context({ [name]: id }),
          )
        ).status,
      ).toBe(200);
      expect(fetcher.mock.calls[0][0]).toBe(
        `${api}/v1/user/notifications/chimely/v1/inbox/${name === "notification_id" ? "notifications" : "broadcasts"}/${id}/read`,
      );
    },
  );

  it("fails privately without the configured user service", async () => {
    vi.stubEnv("DEVFEED_USER_API_URL", "");
    const response = await me.GET(new Request(`${origin}/api`), {});
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "User service unavailable" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});

describe("reader body byte limits and total deadlines", () => {
  it("preserves the exact multipart avatar boundary and bytes", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(Response.json({ avatar_url: "https://images.test/avatar.webp" }));
    vi.stubGlobal("fetch", fetcher);
    const body = new FormData();
    body.append("file", new Blob([new Uint8Array(1_100_000)], { type: "image/png" }), "avatar.png");
    const incoming = new Request(`${origin}/api`, {
      method: "POST",
      headers: { Origin: origin, "X-CSRF-Token": "csrf" },
      body,
    });
    const expected = await incoming.clone().arrayBuffer();
    expect((await avatar.POST(incoming, {})).status).toBe(200);
    expect(fetcher.mock.calls[0][1].headers.get("content-type")).toBe(
      incoming.headers.get("content-type"),
    );
    expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
    expect(Buffer.from(fetcher.mock.calls[0][1].body).equals(Buffer.from(expected))).toBe(true);
  });

  it.each([
    { name: "ordinary preferences", handler: preferences.PUT, method: "PUT", limit: 1_000_000 },
    { name: "avatar upload", handler: avatar.POST, method: "POST", limit: 5 * 1024 * 1024 + 65536 },
    { name: "avatar removal", handler: avatar.DELETE, method: "DELETE", limit: 1_000_000 },
  ])(
    "accepts the exact byte boundary and rejects declared/actual oversize for $name",
    async ({ handler, method, limit }) => {
      const fetcher = vi.fn().mockImplementation(() => Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      const exact = new Uint8Array(limit);
      const accepted = await handler(
        new Request(`${origin}/api`, { method, headers: { Origin: origin }, body: exact }),
        {},
      );
      expect(accepted.status).toBe(200);
      expect(fetcher.mock.calls[0][1].body.byteLength).toBe(limit);
      expect(Buffer.from(fetcher.mock.calls[0][1].body).equals(Buffer.from(exact))).toBe(true);
      fetcher.mockClear();
      for (const declared of [true, false]) {
        const response = await handler(
          new Request(`${origin}/api`, {
            method,
            headers: {
              Origin: origin,
              ...(declared ? { "Content-Length": String(limit + 1) } : {}),
            },
            body: new Uint8Array(limit + 1),
          }),
          {},
        );
        expect(response.status).toBe(413);
        expect(await response.json()).toEqual({ detail: "Request too large" });
        expect(response.headers.get("cache-control")).toBe("no-store");
      }
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each([
    { name: "ordinary preferences", handler: preferences.PUT, method: "PUT", deadline: 2000 },
    { name: "avatar upload", handler: avatar.POST, method: "POST", deadline: 10_000 },
    { name: "avatar removal", handler: avatar.DELETE, method: "DELETE", deadline: 2000 },
  ])(
    "cancels and unlocks a stalled $name body at its total deadline",
    async ({ handler, method, deadline }) => {
      vi.useFakeTimers();
      const fetcher = vi.fn();
      const cancel = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method,
        headers: { Origin: origin },
        body: new ReadableStream<Uint8Array>({ cancel }),
        duplex: "half",
      } as RequestInit);
      const completed = vi.fn();
      const pending = handler(incoming, {});
      void pending.then(completed);
      await vi.advanceTimersByTimeAsync(deadline - 1);
      expect(completed).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      const response = await pending;
      expect(response.status).toBe(408);
      expect(await response.json()).toEqual({ detail: "Incomplete request" });
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(cancel).toHaveBeenCalledOnce();
      expect(incoming.body!.locked).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("does not reset the ordinary deadline when partial chunks keep arriving", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const cancel = vi.fn();
    const incoming = new Request(`${origin}/api`, {
      method: "PUT",
      headers: { Origin: origin },
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          stream = controller;
        },
        cancel,
      }),
      duplex: "half",
    } as RequestInit);
    const pending = preferences.PUT(incoming, {});
    await vi.advanceTimersByTimeAsync(1000);
    stream.enqueue(new TextEncoder().encode('{"topic_ids":'));
    await vi.advanceTimersByTimeAsync(999);
    stream.enqueue(new TextEncoder().encode("["));
    expect(fetcher).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect((await pending).status).toBe(408);
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
  });

  it.each(["stalls", "rejects"])(
    "still returns 408 when body cancellation %s",
    async (behavior) => {
      vi.useFakeTimers();
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const cancel = vi.fn(() =>
        behavior === "stalls"
          ? new Promise<void>(() => {})
          : Promise.reject(new Error("Private peer reason")),
      );
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { Origin: origin },
        body: new ReadableStream<Uint8Array>({ cancel }),
        duplex: "half",
      } as RequestInit);
      const pending = logout.POST(incoming, {});
      await vi.advanceTimersByTimeAsync(2000);
      expect((await pending).status).toBe(408);
      expect(cancel).toHaveBeenCalledOnce();
      expect(incoming.body!.locked).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("forwards complete preference JSON and CSRF without adding fields", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ topic_ids: [] }));
    vi.stubGlobal("fetch", fetcher);
    const incoming = new Request(`${origin}/api`, {
      method: "PUT",
      headers: { Origin: origin, "X-CSRF-Token": "csrf", "Content-Type": "application/json" },
      body: '{"topic_ids":[]}',
    });
    expect((await preferences.PUT(incoming, {})).status).toBe(200);
    expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
    expect(new TextDecoder().decode(fetcher.mock.calls[0][1].body)).toBe('{"topic_ids":[]}');
  });
});

describe("dedicated personalized placement handler", () => {
  it("validates and forwards the exact 16384-byte context while keeping only session and CSRF", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [] }));
    vi.stubGlobal("fetch", fetcher);
    const body = JSON.stringify({ surface: "feed" }).padEnd(16384, " ");
    const response = await placements.POST(
      new Request(`${origin}/api`, {
        method: "POST",
        headers: {
          Origin: origin,
          "Content-Type": "application/json",
          Cookie: "devfeed_user_session=session; admin_session=private",
          "X-CSRF-Token": "csrf",
        },
        body,
      }),
      {},
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ items: [] });
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe(`${api}/v1/user/partner-placements/query`);
    expect(new TextDecoder().decode(options.body)).toBe(body);
    expect(options.headers.get("cookie")).toBe("devfeed_user_session=session");
    expect(options.headers.get("x-csrf-token")).toBe("csrf");
    expect(options.cache).toBe("no-store");
  });

  it.each([
    "{",
    JSON.stringify({ surface: "feed", user_id: "untrusted" }),
    JSON.stringify({ surface: "article" }),
  ])("rejects invalid placement context before forwarding: %s", async (body) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await placements.POST(
      new Request(`${origin}/api`, { method: "POST", headers: { Origin: origin }, body }),
      {},
    );
    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({ detail: "Invalid placement context" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "rejects oversized placement context before parsing (declared=%s)",
    async (declared) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const response = await placements.POST(
        new Request(`${origin}/api`, {
          method: "POST",
          headers: { Origin: origin, ...(declared ? { "Content-Length": "16385" } : {}) },
          body: " ".repeat(16385),
        }),
        {},
      );
      expect(response.status).toBe(413);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("ends a personalized placement body at two seconds and cancels its reader", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn();
    const cancel = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const incoming = new Request(`${origin}/api`, {
      method: "POST",
      headers: { Origin: origin },
      body: new ReadableStream<Uint8Array>({ cancel }),
      duplex: "half",
    } as RequestInit);
    const pending = placements.POST(incoming, {});
    await vi.advanceTimersByTimeAsync(2000);
    expect((await pending).status).toBe(408);
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

it("forwards bounded acquisition cookies only to login and callback independently of X", async () => {
  vi.stubEnv("DEVFEED_READER_MEASUREMENT_ENABLED", "true");
  const fetcher = vi.fn().mockImplementation(() => Response.json({}));
  vi.stubGlobal("fetch", fetcher);
  const cookies =
    "devfeed_user_acquisition=bounded; __Host-devfeed_user_acquisition=secure; devfeed_user_x_click=private";
  for (const handler of [login.GET, callback.GET, me.GET])
    await handler(new Request(`${origin}/api`, { headers: { Cookie: cookies } }), {});
  expect(fetcher.mock.calls[0][1].headers.get("cookie")).toBe(
    "devfeed_user_acquisition=bounded; __Host-devfeed_user_acquisition=secure",
  );
  expect(fetcher.mock.calls[1][1].headers.get("cookie")).toBe(
    "devfeed_user_acquisition=bounded; __Host-devfeed_user_acquisition=secure",
  );
  expect(fetcher.mock.calls[2][1].headers.get("cookie")).toBeNull();
});

it("preserves canonical privacy signals only on reader measurement operations", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json({}));
  vi.stubGlobal("fetch", fetcher);
  for (const handler of [login.GET, callback.GET, me.GET])
    await handler(
      new Request(`${origin}/api`, {
        headers: { DNT: "1", "Sec-GPC": "1", "Private-Header": "private" },
      }),
      {},
    );
  expect(fetcher.mock.calls[0][1].headers.get("dnt")).toBe("1");
  expect(fetcher.mock.calls[1][1].headers.get("sec-gpc")).toBe("1");
  expect(fetcher.mock.calls[2][1].headers.get("dnt")).toBeNull();
  expect(fetcher.mock.calls[0][1].headers.get("private-header")).toBeNull();
});

it("forwards a validated current measurement only on the original-open operation", async () => {
  vi.stubEnv("DEVFEED_READER_MEASUREMENT_ENABLED", "true");
  const value = {
    version: 1,
    status: "direct",
    source: "direct",
    medium: "none",
    campaign: null,
    landing_route: "home",
    captured_at: Math.floor(Date.now() / 1000),
    client_platform: "web",
    measurement: "eligible",
    client_id: "123.456",
    session_id: 789,
  };
  const fetcher = vi.fn().mockImplementation(() => Response.json({}));
  vi.stubGlobal("fetch", fetcher);
  await articleOpen.POST(
    new Request(`${origin}/api`, {
      method: "POST",
      headers: { Origin: origin, "X-Devfeed-Reader-Measurement": JSON.stringify(value), DNT: "1" },
    }),
    context({ article_id: articleId }),
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].headers.get("x-devfeed-reader-measurement"))).toEqual(
    value,
  );
  expect(fetcher.mock.calls[0][1].headers.get("dnt")).toBe("1");
  await me.GET(
    new Request(`${origin}/api`, {
      headers: { "X-Devfeed-Reader-Measurement": JSON.stringify(value) },
    }),
    {},
  );
  expect(fetcher.mock.calls[1][1].headers.get("x-devfeed-reader-measurement")).toBeNull();
  await articleOpen.POST(
    new Request(`${origin}/api`, {
      method: "POST",
      headers: {
        Origin: origin,
        "X-Devfeed-Reader-Measurement": JSON.stringify({ ...value, email: "private@example.test" }),
      },
    }),
    context({ article_id: articleId }),
  );
  expect(fetcher.mock.calls[2][1].headers.get("x-devfeed-reader-measurement")).toBeNull();
});
