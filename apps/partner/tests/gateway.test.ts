import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import * as productCollection from "../src/app/api/v1/partner/accounts/[account_id]/products/route";
import * as product from "../src/app/api/v1/partner/accounts/[account_id]/products/[product_id]/route";
import * as productImage from "../src/app/api/v1/partner/accounts/[account_id]/products/[product_id]/ad-image/route";
import * as productPromotion from "../src/app/api/v1/partner/accounts/[account_id]/products/[product_id]/promotion/route";
import * as preferences from "../src/app/api/v1/partner/accounts/[account_id]/preferences/route";
import * as destinationPreview from "../src/app/api/v1/partner/accounts/[account_id]/destination-preview/route";
import * as creativePreview from "../src/app/api/v1/partner/accounts/[account_id]/creative-preview/route";
import * as audienceTopics from "../src/app/api/v1/partner/accounts/[account_id]/audience-topics/route";
import * as accountInventory from "../src/app/api/v1/partner/accounts/[account_id]/inventory/route";
import * as workspaceInventory from "../src/app/api/v1/partner/inventory/route";
import * as workspaceDashboard from "../src/app/api/v1/partner/dashboard/route";
import * as workspaceConnections from "../src/app/api/v1/partner/connections/route";
import * as accountConnections from "../src/app/api/v1/partner/accounts/[account_id]/connections/route";
import * as connection from "../src/app/api/v1/partner/accounts/[account_id]/connections/[provider]/route";
import * as connectionActions from "../src/app/api/v1/partner/accounts/[account_id]/connections/[provider]/actions/route";
import * as draftPreview from "../src/app/api/v1/partner/accounts/[account_id]/connections/connector-preview/route";
import * as draftResponse from "../src/app/api/v1/partner/accounts/[account_id]/connections/connector-response/route";
import * as connectionPreview from "../src/app/api/v1/partner/accounts/[account_id]/connections/[provider]/connector-preview/route";
import * as connectionResponse from "../src/app/api/v1/partner/accounts/[account_id]/connections/[provider]/connector-response/route";
import * as logout from "../src/app/api/v1/partner/auth/logout/route";
import * as callback from "../src/app/api/v1/partner/auth/callback/route";
import * as me from "../src/app/api/v1/partner/auth/me/route";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));

const accountId = "11111111-1111-4111-8111-111111111111";
const productId = "22222222-2222-4222-8222-222222222222";
const accountParams = { account_id: accountId };
const productParams = { ...accountParams, product_id: productId };
const connectionParams = { ...accountParams, provider: "catalog-connection" };
const origin = "https://partner.example";
const api = "http://partner-api:8004";
type Params = Record<string, string | string[]>;
const context = (params: Params = {}) =>
  Object.keys(params).length ? { params: Promise.resolve(params) } : {};

beforeEach(() => {
  vi.stubEnv("DEVFEED_PARTNER_API_URL", api);
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", origin);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("concrete partner operation transport", () => {
  it.each([{}, { params: undefined }])(
    "forwards static GET and POST handlers with Next's parameterless context: %j",
    async (routeContext) => {
      const fetcher = vi.fn().mockImplementation(() => Response.json({ ok: true }));
      vi.stubGlobal("fetch", fetcher);
      expect((await me.GET(new Request(`${origin}/api`), routeContext)).status).toBe(200);
      expect(
        (
          await logout.POST(
            new Request(`${origin}/api`, { method: "POST", headers: { origin }, body: "{}" }),
            routeContext,
          )
        ).status,
      ).toBe(200);
      expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
        `${api}/v1/partner/auth/me`,
        `${api}/v1/partner/auth/logout`,
      ]);
    },
  );

  it.each([{}, { params: undefined }])(
    "rejects missing dynamic context before consuming the body: %j",
    async (routeContext) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { origin },
        body: new ReadableStream<Uint8Array>(),
        duplex: "half",
      } as RequestInit);
      expect((await productImage.POST(incoming, routeContext)).status).toBe(404);
      expect(incoming.bodyUsed).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("preserves multipart boundaries, bytes and CSRF headers for the bound product image operation", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ad_image_status: "ready" }));
    vi.stubGlobal("fetch", fetcher);
    const body = new FormData();
    body.append("file", new Blob([new Uint8Array(1_100_000)], { type: "image/png" }), "ad.png");
    const incoming = new Request(`${origin}/api`, {
      method: "POST",
      headers: { origin, "x-csrf-token": "csrf" },
      body,
    });
    const expected = await incoming.clone().arrayBuffer();
    expect((await productImage.POST(incoming, context(productParams))).status).toBe(200);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`${api}/v1/partner/accounts/${accountId}/products/${productId}/ad-image`);
    expect(init.headers.get("content-type")).toBe(incoming.headers.get("content-type"));
    expect(init.headers.get("x-csrf-token")).toBe("csrf");
    expect(Buffer.from(init.body).equals(Buffer.from(expected))).toBe(true);
  });

  it.each([
    {
      name: "create product",
      handler: productCollection.POST,
      params: accountParams,
      limit: 65536,
      method: "POST",
    },
    {
      name: "update product",
      handler: product.PATCH,
      params: productParams,
      limit: 65536,
      method: "PATCH",
    },
    {
      name: "archive product",
      handler: product.DELETE,
      params: productParams,
      limit: 65536,
      method: "DELETE",
    },
    {
      name: "product image",
      handler: productImage.POST,
      params: productParams,
      limit: 5 * 1024 * 1024 + 65536,
      method: "POST",
    },
    {
      name: "connection credentials",
      handler: connection.PUT,
      params: connectionParams,
      limit: 1_000_000,
      method: "PUT",
    },
    { name: "logout", handler: logout.POST, params: {}, limit: 1_000_000, method: "POST" },
  ])(
    "enforces the declared and actual byte limit for $name",
    async ({ handler, params, limit, method }) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      for (const declared of [true, false]) {
        const incoming = new Request(`${origin}/api`, {
          method,
          headers: { origin, ...(declared ? { "content-length": String(limit + 1) } : {}) },
          body: new Uint8Array(limit + 1),
        });
        const response = await handler(incoming, context(params));
        expect(response.status).toBe(413);
        expect(await response.json()).toEqual({ detail: "Request too large" });
        expect(response.headers.get("cache-control")).toBe("no-store");
      }
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("preserves multilingual product facts inside the write limit", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetcher);
    const facts = { name: "Product", description: "界".repeat(5000), expected_revision: 2 };
    const response = await product.PATCH(
      new Request(`${origin}/api`, {
        method: "PATCH",
        headers: { origin, "content-type": "application/json" },
        body: JSON.stringify(facts),
      }),
      context(productParams),
    );
    expect(response.status).toBe(200);
    expect(JSON.parse(new TextDecoder().decode(fetcher.mock.calls[0][1].body))).toEqual(facts);
  });

  it.each([
    {
      name: "create product",
      handler: productCollection.POST,
      params: accountParams,
      method: "POST",
    },
    { name: "update product", handler: product.PATCH, params: productParams, method: "PATCH" },
    { name: "archive product", handler: product.DELETE, params: productParams, method: "DELETE" },
    { name: "upload image", handler: productImage.POST, params: productParams, method: "POST" },
    { name: "preferences", handler: preferences.PUT, params: accountParams, method: "PUT" },
    {
      name: "product promotion",
      handler: productPromotion.PUT,
      params: productParams,
      method: "PUT",
    },
    {
      name: "account connection creation",
      handler: accountConnections.POST,
      params: accountParams,
      method: "POST",
    },
    {
      name: "workspace connection creation",
      handler: workspaceConnections.POST,
      params: {},
      method: "POST",
    },
    { name: "connection update", handler: connection.PUT, params: connectionParams, method: "PUT" },
    {
      name: "connection removal",
      handler: connection.DELETE,
      params: connectionParams,
      method: "DELETE",
    },
    {
      name: "connection action",
      handler: connectionActions.POST,
      params: connectionParams,
      method: "POST",
    },
    { name: "draft preview", handler: draftPreview.POST, params: accountParams, method: "POST" },
    { name: "draft response", handler: draftResponse.POST, params: accountParams, method: "POST" },
    {
      name: "connection preview",
      handler: connectionPreview.POST,
      params: connectionParams,
      method: "POST",
    },
    {
      name: "connection response",
      handler: connectionResponse.POST,
      params: connectionParams,
      method: "POST",
    },
    { name: "logout", handler: logout.POST, params: {}, method: "POST" },
  ])(
    "rejects missing or foreign origins before reading $name bodies",
    async ({ handler, params, method }) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      for (const suppliedOrigin of [undefined, "https://evil.example"]) {
        const incoming = new Request(`${origin}/api`, {
          method,
          headers: {
            ...(suppliedOrigin ? { origin: suppliedOrigin } : {}),
            "x-csrf-token": "csrf",
          },
          body: new ReadableStream<Uint8Array>(),
          duplex: "half",
        } as RequestInit);
        const response = await handler(incoming, context(params));
        expect(response.status).toBe(403);
        expect(await response.json()).toEqual({ detail: "Invalid request origin" });
        expect(incoming.bodyUsed).toBe(false);
      }
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each([
    {},
    { account_id: "not-a-uuid", product_id: productId },
    { accountId, productId },
    { account_id: [accountId], product_id: productId },
    { account_id: accountId, product_id: [productId] },
    { account_id: accountId, product_id: "../auth/logout" },
    { account_id: accountId, product_id: "%2e%2e" },
  ] as Params[])(
    "rejects invalid bound product parameters before body consumption: %j",
    async (params) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { origin },
        body: new ReadableStream<Uint8Array>(),
        duplex: "half",
      } as RequestInit);
      expect((await productImage.POST(incoming, context(params))).status).toBe(404);
      expect(incoming.bodyUsed).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each(["..", ".", "a/b", "a\\b", "%2f", "a?query", "a#fragment", "a\0b", "\ud800"])(
    "rejects unsafe dynamic connection parameters: %j",
    async (provider) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      expect(
        (
          await connection.GET(
            new Request(`${origin}/api`),
            context({ ...accountParams, provider }),
          )
        ).status,
      ).toBe(404);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("uses the bound operation and decoded params rather than the request URL to choose the service", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetcher);
    const response = await connection.GET(
      new Request(`${origin}/api/v1/admin/settings?offset=25`, {
        headers: { host: "evil.example", "x-forwarded-host": "evil.example" },
      }),
      context({ ...accountParams, provider: "inventory" }),
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0]).toBe(
      `${api}/v1/partner/accounts/${accountId}/connections/inventory?offset=25`,
    );
    expect(fetcher.mock.calls[0][1].headers.has("host")).toBe(false);
    expect(fetcher.mock.calls[0][1].headers.has("x-forwarded-host")).toBe(false);
  });

  it.each([401, 403, 404, 422, 429])(
    "preserves backend authorization and validation errors (%s)",
    async (status) => {
      const upstream = Response.json(
        { detail: "Access or validation denied" },
        { status, headers: { "Retry-After": "10" } },
      );
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream));
      const response = await connection.GET(
        new Request(`${origin}/api`),
        context(connectionParams),
      );
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual({ detail: "Access or validation denied" });
      expect(response.headers.get("retry-after")).toBe("10");
      expect(response.headers.get("cache-control")).toBe("no-store");
    },
  );

  it("preserves partial account/product preferences and write-only connection credentials", async () => {
    const fetcher = vi.fn().mockImplementation(() => Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    for (const { handler, params, payload } of [
      {
        handler: preferences.PUT,
        params: accountParams,
        payload: {
          audience: { experience_levels: ["senior"], language_topic_ids: [], tool_topic_ids: [] },
        },
      },
      {
        handler: productPromotion.PUT,
        params: productParams,
        payload: { ad_formats: [], promotion_surfaces: null },
      },
      {
        handler: connection.PUT,
        params: connectionParams,
        payload: { expected_revision: 3, credential: "fixture-write-only" },
      },
    ]) {
      expect(
        (
          await handler(
            new Request(`${origin}/api`, {
              method: "PUT",
              headers: {
                origin,
                cookie: "partner_session=private",
                "x-csrf-token": "csrf",
                "content-type": "application/json",
                authorization: "never-forward",
              },
              body: JSON.stringify(payload),
            }),
            context(params),
          )
        ).status,
      ).toBe(200);
      const init = fetcher.mock.calls.at(-1)![1];
      expect(JSON.parse(new TextDecoder().decode(init.body))).toEqual(payload);
      expect(init.headers.get("cookie")).toBe("partner_session=private");
      expect(init.headers.get("x-csrf-token")).toBe("csrf");
      expect(init.headers.has("authorization")).toBe(false);
    }
  });

  it.each([
    {
      name: "destination preview",
      handler: destinationPreview.GET,
      params: accountParams,
      path: `/accounts/${accountId}/destination-preview`,
    },
    {
      name: "creative preview",
      handler: creativePreview.GET,
      params: accountParams,
      path: `/accounts/${accountId}/creative-preview`,
    },
    {
      name: "audience search",
      handler: audienceTopics.GET,
      params: accountParams,
      path: `/accounts/${accountId}/audience-topics`,
    },
    {
      name: "account inventory",
      handler: accountInventory.GET,
      params: accountParams,
      path: `/accounts/${accountId}/inventory`,
    },
    {
      name: "workspace inventory",
      handler: workspaceInventory.GET,
      params: {},
      path: "/inventory",
    },
    {
      name: "workspace dashboard",
      handler: workspaceDashboard.GET,
      params: {},
      path: "/dashboard",
    },
  ])(
    "forwards $name query parameters and safe read headers without a body",
    async ({ handler, params, path }) => {
      const fetcher = vi.fn().mockResolvedValue(Response.json({}));
      vi.stubGlobal("fetch", fetcher);
      expect(
        (
          await handler(
            new Request(`${origin}/api?dimension=tools&q=docker&offset=25&limit=25`, {
              headers: {
                cookie: "partner_session=private",
                authorization: "never-forward",
                "if-none-match": "tag",
                "last-event-id": "resume",
                "x-chimely-subscriber-hash": "forged",
              },
            }),
            context(params),
          )
        ).status,
      ).toBe(200);
      const [url, init] = fetcher.mock.calls[0];
      expect(url).toBe(`${api}/v1/partner${path}?dimension=tools&q=docker&offset=25&limit=25`);
      expect(init.body).toBeUndefined();
      expect(init.headers.get("cookie")).toBe("partner_session=private");
      expect(init.headers.get("if-none-match")).toBe("tag");
      expect(init.headers.get("last-event-id")).toBe("resume");
      expect(init.headers.has("authorization")).toBe(false);
      expect(init.headers.has("x-chimely-subscriber-hash")).toBe(false);
      expect(init.cache).toBe("no-store");
    },
  );

  it("preserves callback redirects and every independent cookie without leaking response headers", async () => {
    const upstream = new Response(null, {
      status: 302,
      headers: {
        location: `${origin}/`,
        "x-request-id": "request-1",
        "x-devfeed-version": "test",
        "x-private": "never-forward",
      },
    });
    upstream.headers.append("set-cookie", "session=new; HttpOnly");
    upstream.headers.append("set-cookie", "state=; Max-Age=0");
    const fetcher = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetcher);
    const response = await callback.GET(
      new Request(`${origin}/api?code=private&state=private`, {
        headers: { cookie: "state=private", authorization: "never-forward" },
      }),
      context(),
    );
    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe(`${origin}/`);
    expect(response.headers.getSetCookie()).toEqual(upstream.headers.getSetCookie());
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("x-request-id")).toBe("request-1");
    expect(response.headers.get("x-devfeed-version")).toBe("test");
    expect(response.headers.has("x-private")).toBe(false);
    expect(fetcher.mock.calls[0][1].redirect).toBe("manual");
    expect(fetcher.mock.calls[0][1].headers.has("authorization")).toBe(false);
  });

  it("returns a streaming upstream body without waiting for the stream to finish", async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, {
          headers: {
            "content-type": "text/event-stream",
            "x-accel-buffering": "no",
            etag: '"tag"',
          },
        }),
      ),
    );
    const response = await me.GET(new Request(`${origin}/api`), context());
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(response.headers.get("etag")).toBe('"tag"');
    const reader = response.body!.getReader();
    stream.enqueue(new TextEncoder().encode("data: first\n\n"));
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: first\n\n");
    stream.enqueue(new TextEncoder().encode("data: second\n\n"));
    stream.close();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("data: second\n\n");
    expect((await reader.read()).done).toBe(true);
    reader.releaseLock();
  });

  it("reports transport failure without exposing private upstream details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Private URL/cookie/token")));
    const response = await logout.POST(
      new Request(`${origin}/api`, { method: "POST", headers: { origin }, body: "{}" }),
      context(),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "Partner service unavailable" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});

describe("bounded body reading and request cancellation", () => {
  it.each([
    {
      name: "image",
      handler: productImage.POST,
      params: productParams,
      method: "POST",
      deadline: 10_000,
    },
    {
      name: "preferences",
      handler: preferences.PUT,
      params: accountParams,
      method: "PUT",
      deadline: 2000,
    },
    {
      name: "connection creation",
      handler: accountConnections.POST,
      params: accountParams,
      method: "POST",
      deadline: 2000,
    },
  ])(
    "returns 408 and releases an incomplete $name body at its deadline",
    async ({ handler, params, method, deadline }) => {
      vi.useFakeTimers();
      const fetcher = vi.fn();
      const cancel = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`${origin}/api`, {
        method,
        headers: { origin },
        body: new ReadableStream<Uint8Array>({ cancel }),
        duplex: "half",
      } as RequestInit);
      const completed = vi.fn();
      const pending = handler(incoming, context(params));
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

  it.each(["resolves", "stalls", "rejects"])(
    "keeps the body deadline bounded when cancellation %s",
    async (behavior) => {
      vi.useFakeTimers();
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const cancel = vi.fn(() =>
        behavior === "stalls"
          ? new Promise<void>(() => {})
          : behavior === "rejects"
            ? Promise.reject(new Error("Private peer details"))
            : Promise.resolve(),
      );
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { origin },
        body: new ReadableStream<Uint8Array>({ cancel }),
        duplex: "half",
      } as RequestInit);
      const pending = logout.POST(incoming, context());
      await vi.advanceTimersByTimeAsync(2000);
      expect((await pending).status).toBe(408);
      expect(cancel).toHaveBeenCalledOnce();
      expect(incoming.body!.locked).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("uses one total deadline when partial preference chunks keep arriving", async () => {
    vi.useFakeTimers();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const cancel = vi.fn();
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const incoming = new Request(`${origin}/api`, {
      method: "PUT",
      headers: { origin, "content-type": "application/json" },
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          stream = controller;
        },
        cancel,
      }),
      duplex: "half",
    } as RequestInit);
    const pending = preferences.PUT(incoming, context(accountParams));
    const completed = vi.fn();
    void pending.then(completed);
    await vi.advanceTimersByTimeAsync(1000);
    stream.enqueue(new TextEncoder().encode('{"outbound_destination":'));
    await vi.advanceTimersByTimeAsync(999);
    stream.enqueue(new TextEncoder().encode('"listing_url"'));
    expect(completed).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect((await pending).status).toBe(408);
    expect(cancel).toHaveBeenCalledOnce();
    expect(incoming.body!.locked).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    { declared: true, cancellation: "stalls" },
    { declared: true, cancellation: "rejects" },
    { declared: false, cancellation: "stalls" },
    { declared: false, cancellation: "rejects" },
  ])(
    "keeps oversized bodies at 413 when cancellation $cancellation (declared=$declared)",
    async ({ declared, cancellation }) => {
      vi.useFakeTimers();
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const cancel = vi.fn(() =>
        cancellation === "stalls"
          ? new Promise<void>(() => {})
          : Promise.reject(new Error("Private peer details")),
      );
      const incoming = new Request(`${origin}/api`, {
        method: "POST",
        headers: { origin, "content-length": declared ? "1000001" : "1" },
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("é".repeat(250_000)));
            controller.enqueue(new TextEncoder().encode("é".repeat(250_001)));
          },
          cancel,
        }),
        duplex: "half",
      } as RequestInit);
      const pending = logout.POST(incoming, context());
      await vi.advanceTimersByTimeAsync(0);
      const response = await pending;
      expect(response.status).toBe(413);
      expect(cancel).toHaveBeenCalledOnce();
      expect(incoming.body!.locked).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("forwards completed chunked bodies at the byte limit and clears their deadline", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const value = "é".repeat(500_000);
    const bytes = new TextEncoder().encode(value);
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const cancel = vi.fn();
    const incoming = new Request(`${origin}/api`, {
      method: "POST",
      headers: { origin, "x-csrf-token": "csrf" },
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          stream = controller;
        },
        cancel,
      }),
      duplex: "half",
    } as RequestInit);
    const pending = logout.POST(incoming, context());
    await vi.advanceTimersByTimeAsync(400);
    stream.enqueue(bytes.slice(0, 400_001));
    await vi.advanceTimersByTimeAsync(400);
    expect(fetcher).not.toHaveBeenCalled();
    stream.enqueue(bytes.slice(400_001));
    stream.close();
    expect((await pending).status).toBe(204);
    expect(fetcher.mock.calls[0][1].body.byteLength).toBe(1_000_000);
    expect(new TextDecoder().decode(fetcher.mock.calls[0][1].body)).toBe(value);
    expect(incoming.body!.locked).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(2000);
    expect(cancel).not.toHaveBeenCalled();
  });

  it("does not call the API for an already cancelled reader request", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const caller = new AbortController();
    caller.abort(new Error("Private cancellation reason"));
    const response = await me.GET(
      new Request(`${origin}/api`, { signal: caller.signal }),
      context(),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "Partner service unavailable" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(["caller", "deadline"])(
    "cancels an in-flight upstream request when its %s aborts",
    async (source) => {
      vi.useFakeTimers();
      const caller = new AbortController();
      vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
        const timer = new AbortController();
        setTimeout(() => timer.abort(new Error("Private upstream timeout")), milliseconds);
        return timer.signal;
      });
      const fetcher = vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal!.addEventListener("abort", () => reject(new Error("Private request URL")), {
              once: true,
            });
          }),
      );
      vi.stubGlobal("fetch", fetcher);
      const pending = me.GET(new Request(`${origin}/api`, { signal: caller.signal }), context());
      await vi.advanceTimersByTimeAsync(0);
      expect(fetcher).toHaveBeenCalledOnce();
      expect(AbortSignal.timeout).toHaveBeenCalledWith(45_000);
      if (source === "caller") caller.abort();
      else {
        const completed = vi.fn();
        void pending.then(completed);
        await vi.advanceTimersByTimeAsync(44_999);
        expect(completed).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(1);
      }
      const response = await pending;
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ detail: "Partner service unavailable" });
      expect(fetcher.mock.calls[0][1].signal!.aborted).toBe(true);
    },
  );
});
