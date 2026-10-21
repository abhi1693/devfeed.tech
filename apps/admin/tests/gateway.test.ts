import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { gateway } from "@/lib/server/gateway";
import { adminFetch, ApiError, returnToLogin } from "@/lib/api/client";
import { adminAuthLogout } from "@/lib/api/generated/admin";

const productAdImage = [
  "v1",
  "admin",
  "partner-accounts",
  "11111111-1111-4111-8111-111111111111",
  "products",
  "22222222-2222-4222-8222-222222222222",
  "ad-image",
];

beforeEach(() => {
  vi.stubEnv("DEVFEED_ADMIN_BASE_URL", "https://admin.example");
  vi.stubEnv("DEVFEED_ADMIN_API_URL", "http://admin-api.internal:8001");
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("isolated API gateway", () => {
  it.each([
    ["POST", productAdImage.slice(0, -2)],
    ["PATCH", productAdImage.slice(0, -1)],
  ])("caps %s product facts before proxying", async (method, path) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    for (const declared of [true, false]) {
      const incoming = new Request("https://admin.example/api", {
        method,
        headers: {
          origin: "https://admin.example",
          ...(declared ? { "content-length": "65537" } : {}),
        },
        body: new Uint8Array(65537),
      });
      const response = await gateway(incoming, path);
      expect(response.status).toBe(413);
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("preserves multilingual product taglines within the product body limit", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ id: productAdImage[5] }));
    vi.stubGlobal("fetch", fetcher);
    const body = JSON.stringify({
      name: "Developer tools",
      product_url: "https://product.example",
      tagline: "開".repeat(160),
    });
    const response = await gateway(
      new Request("https://admin.example/api", {
        method: "POST",
        headers: { origin: "https://admin.example", "content-type": "application/json" },
        body,
      }),
      productAdImage.slice(0, -2),
    );
    expect(response.status).toBe(200);
    expect(new TextDecoder().decode(fetcher.mock.calls[0][1].body)).toBe(body);
  });

  it("preserves multipart image bytes and grants the larger limit only to the upload route", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ad_image_status: "ready" }));
    vi.stubGlobal("fetch", fetcher);
    const body = new FormData();
    body.append("file", new Blob([new Uint8Array(1_100_000)], { type: "image/png" }), "ad.png");
    const incoming = new Request("https://admin.example/api", {
      method: "POST",
      headers: { origin: "https://admin.example", "x-csrf-token": "csrf" },
      body,
    });
    const expectedBytes = await incoming.clone().arrayBuffer();
    expect((await gateway(incoming, productAdImage)).status).toBe(200);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`http://admin-api.internal:8001/${productAdImage.join("/")}`);
    expect(init.headers.get("content-type")).toBe(incoming.headers.get("content-type"));
    expect(init.headers.get("x-csrf-token")).toBe("csrf");
    expect(Buffer.from(init.body).equals(Buffer.from(expectedBytes))).toBe(true);
    fetcher.mockClear();
    expect(
      (
        await gateway(
          new Request("https://admin.example/api", {
            method: "POST",
            headers: { origin: "https://admin.example" },
            body: new Uint8Array(1_100_000),
          }),
          ["v1", "admin", "sources"],
        )
      ).status,
    ).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([true, false])("caps oversized image bodies (declared=%s)", async (declared) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const bytes = 5 * 1024 * 1024 + 65537;
    const incoming = new Request("https://admin.example/api", {
      method: "POST",
      headers: {
        origin: "https://admin.example",
        ...(declared ? { "content-length": String(bytes) } : {}),
      },
      body: new Uint8Array(bytes),
    });
    expect((await gateway(incoming, productAdImage)).status).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    [productAdImage, 10_000],
    [productAdImage.slice(0, -2), 2000],
    [["v1", "admin", "sources"], 2000],
  ])("bounds incomplete request bodies at %s", async (path, deadline) => {
    vi.useFakeTimers();
    const fetcher = vi.fn();
    const cancel = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const incoming = new Request("https://admin.example/api", {
      method: "POST",
      headers: { origin: "https://admin.example" },
      body: new ReadableStream<Uint8Array>({ cancel }),
      duplex: "half",
    } as RequestInit);
    const pending = gateway(incoming, path);
    await vi.advanceTimersByTimeAsync(deadline);
    const response = await pending;
    expect(response.status).toBe(408);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(cancel).toHaveBeenCalledOnce();
    expect(fetcher).not.toHaveBeenCalled();
    expect(incoming.body!.locked).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["source-imports", 1_100_000, 200],
    ["source-imports", 6_100_001, 413],
    ["sources", 1_100_000, 413],
  ])("bounds %s collection request bodies at %s bytes", async (resource, bytes, status) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(
      new Request(`https://admin.example/api/v1/admin/${resource}`, {
        method: "POST",
        headers: { Origin: "https://admin.example" },
        body: "x".repeat(bytes),
      }),
      ["v1", "admin", resource],
    );
    expect(result.status).toBe(status);
    expect(fetcher).toHaveBeenCalledTimes(status === 200 ? 1 : 0);
  });

  it.each([
    ["POST", "sources", 215_000],
    ["POST", "source-imports", 150_000],
    ["POST", `source-imports/${productAdImage[3]}/review`, 150_000],
    ["POST", "sources/preview", 305_000],
    ["POST", "topic-discovery/github", 120_000],
    ["GET", "sources", 45_000],
    ["POST", `sources/${productAdImage[3]}/fetch`, 45_000],
    ["PATCH", `sources/${productAdImage[3]}`, 45_000],
  ])("uses the operation deadline for %s %s", async (method, resource, expected) => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
    await gateway(
      new Request(`https://admin.example/api/v1/admin/${resource}`, {
        method,
        headers: { Origin: "https://admin.example" },
      }),
      ["v1", "admin", ...resource.split("/")],
    );
    expect(timeout).toHaveBeenCalledWith(expected);
  });

  it.each([
    ["topic-discovery/github-extra", 404],
    ["sources/preview-extra", 405],
    ["missing", 404],
  ])(
    "rejects an unavailable operation before reading or forwarding its body: %s",
    async (resource, status) => {
      const fetcher = vi.fn();
      vi.stubGlobal("fetch", fetcher);
      const incoming = new Request(`https://admin.example/api/v1/admin/${resource}`, {
        method: "POST",
        headers: { Origin: "https://admin.example", "Content-Length": "9999999" },
        body: "unread",
      });
      expect((await gateway(incoming, ["v1", "admin", ...resource.split("/")])).status).toBe(
        status,
      );
      expect(incoming.bodyUsed).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("rejects unsupported methods using the registered operation methods", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await gateway(
      new Request("https://admin.example/api/v1/admin/workers/ai:host.1", { method: "DELETE" }),
      ["v1", "admin", "workers", "ai:host.1"],
    );
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("GET");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    ["sources", 70_000],
    ["sources/preview", 130_000],
    ["topic-discovery/github", 70_000],
  ])("keeps a slow valid %s operation connected until its response", async (resource, delay) => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(
        () => controller.abort(new DOMException("Timed out", "TimeoutError")),
        milliseconds,
      );
      return controller.signal;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input, init) =>
          new Promise<Response>((resolve, reject) => {
            init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
            setTimeout(
              () =>
                resolve(
                  Response.json(
                    { id: "saved-source" },
                    { status: resource === "sources" ? 201 : 200 },
                  ),
                ),
              delay,
            );
          }),
      ),
    );
    const pending = gateway(
      new Request(`https://admin.example/api/v1/admin/${resource}`, {
        method: "POST",
        headers: { Origin: "https://admin.example" },
      }),
      ["v1", "admin", ...resource.split("/")],
    );
    await vi.advanceTimersByTimeAsync(delay);
    const response = await pending;
    expect(response.status).toBe(resource === "sources" ? 201 : 200);
    expect(await response.json()).toEqual({ id: "saved-source" });
  });

  it("still propagates client cancellation during long source validation", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input, init) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
          }),
      ),
    );
    const pending = gateway(
      new Request("https://admin.example/api/v1/admin/sources", {
        method: "POST",
        headers: { Origin: "https://admin.example" },
        signal: controller.signal,
      }),
      ["v1", "admin", "sources"],
    );
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    controller.abort();
    expect((await pending).status).toBe(503);
  });

  it("streams inbox hints and forwards ETags without exposing subscriber credentials", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response("event: hint\ndata: {}\n\n", {
        headers: {
          "Content-Type": "text/event-stream",
          "X-Accel-Buffering": "no",
          ETag: '"etag"',
        },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const request = new Request(
      "https://admin.example/api/v1/admin/notifications/chimely/v1/inbox/stream",
      {
        headers: {
          "If-None-Match": '"old"',
          "Last-Event-ID": "resume",
          "X-Chimely-Subscriber-Hash": "forged",
        },
      },
    );
    const result = await gateway(request, [
      "v1",
      "admin",
      "notifications",
      "chimely",
      "v1",
      "inbox",
      "stream",
    ]);
    expect(result.headers.get("content-type")).toBe("text/event-stream");
    expect(result.headers.get("x-accel-buffering")).toBe("no");
    expect(result.headers.get("etag")).toBe('"etag"');
    expect(await result.text()).toContain("event: hint");
    expect(fetcher.mock.calls[0][1].headers.get("if-none-match")).toBe('"old"');
    expect(fetcher.mock.calls[0][1].headers.get("last-event-id")).toBe("resume");
    expect(fetcher.mock.calls[0][1].headers.get("x-chimely-subscriber-hash")).toBeNull();
  });
  it("preserves both callback cookies and redirects without following them", async () => {
    const upstream = new Response(null, {
      status: 302,
      headers: { Location: "https://admin.example/" },
    });
    upstream.headers.append(
      "Set-Cookie",
      "__Host-devfeed_admin_session=opaque; Secure; HttpOnly; Path=/",
    );
    upstream.headers.append(
      "Set-Cookie",
      "__Host-devfeed_admin_state=; Max-Age=0; Secure; HttpOnly; Path=/",
    );
    const fetcher = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(
      new Request("https://admin.example/api/v1/admin/auth/callback?state=s&code=c", {
        headers: {
          Cookie: "__Host-devfeed_admin_state=browser",
          Authorization: "Bearer untrusted",
          "X-Forwarded-Host": "evil.example",
        },
      }),
      ["v1", "admin", "auth", "callback"],
    );
    expect(result.status).toBe(302);
    expect(result.headers.getSetCookie()).toHaveLength(2);
    expect(result.headers.get("cache-control")).toBe("no-store");
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe("http://admin-api.internal:8001/v1/admin/auth/callback?state=s&code=c");
    expect(options.redirect).toBe("manual");
    expect(options.headers.get("authorization")).toBeNull();
    expect(options.headers.get("x-forwarded-host")).toBeNull();
    expect(options.headers.get("cookie")).toContain("browser");
  });

  it.each([
    ["v1", "sources"],
    ["v1", "admin", "..", "health"],
    ["https:", "evil.example"],
    ["v1", "admin", "%2e%2e", "keys"],
  ])("rejects non-admin and traversal paths %j", async (...segments) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(new Request("https://admin.example/api/test"), segments);
    expect(result.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([null, "https://evil.example"])("rejects writes with origin %s", async (origin) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(
      new Request("https://admin.example/api/v1/admin/auth/logout", {
        method: "POST",
        headers: origin ? { Origin: origin } : {},
      }),
      ["v1", "admin", "auth", "logout"],
    );
    expect(result.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the csrf header and original origin for authorized writes", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(
      new Request("https://admin.example/api/v1/admin/auth/logout", {
        method: "POST",
        headers: { Origin: "https://admin.example", "X-CSRF-Token": "csrf" },
      }),
      ["v1", "admin", "auth", "logout"],
    );
    expect(result.status).toBe(204);
    expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
  });

  it("passes logout cookie deletions to the browser", async () => {
    const upstream = new Response(null, { status: 204 });
    upstream.headers.append(
      "Set-Cookie",
      "__Host-devfeed_admin_session=; Max-Age=0; Secure; HttpOnly; Path=/",
    );
    upstream.headers.append(
      "Set-Cookie",
      "__Host-devfeed_admin_state=; Max-Age=0; Secure; HttpOnly; Path=/",
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream));
    const result = await gateway(
      new Request("https://admin.example/api/v1/admin/auth/logout", {
        method: "POST",
        headers: { Origin: "https://admin.example", "X-CSRF-Token": "csrf" },
      }),
      ["v1", "admin", "auth", "logout"],
    );
    expect(result.status).toBe(204);
    expect(result.headers.getSetCookie()).toEqual(upstream.headers.getSetCookie());
    expect(result.headers.get("cache-control")).toBe("no-store");
  });

  it("has no implicit upstream URL and does not expose errors", async () => {
    vi.stubEnv("DEVFEED_ADMIN_API_URL", "");
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/me"), [
      "v1",
      "admin",
      "auth",
      "me",
    ]);
    expect(result.status).toBe(503);
    expect(await result.json()).toEqual({ detail: "Admin service unavailable" });
  });
});

describe("Orval transport", () => {
  it("shows the safe GitHub failure so an import can be resumed", async () => {
    const message =
      "GitHub took too long to respond. Continue pulling to retry the remaining topics.";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ detail: { code: "github_unavailable", message } }, { status: 503 }),
        ),
    );
    await expect(
      adminFetch("/v1/admin/topic-discovery/github", { method: "POST" }),
    ).rejects.toEqual(new ApiError(503, message));
  });
  it("does not expose unclassified server failure details", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(Response.json({ detail: "private database details" }, { status: 503 })),
    );
    await expect(
      adminFetch("/v1/admin/topic-discovery/github", { method: "POST" }),
    ).rejects.toEqual(new ApiError(503));
  });
  it.each(["report_refreshing", "report_rebuilding"])(
    "recognizes %s without exposing server diagnostic text",
    async (code) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            Response.json({ detail: { code, message: "private diagnostic" } }, { status: 503 }),
          ),
      );
      await expect(adminFetch("/v1/admin/overview/panels/workload")).rejects.toEqual(
        new ApiError(503, "The saved report is being prepared. Please retry shortly.", {}, code),
      );
    },
  );
  it("explains pipeline initialization without exposing server details", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json(
            { detail: { code: "projection_initializing", message: "private diagnostic" } },
            { status: 503 },
          ),
        ),
    );
    await expect(adminFetch("/v1/admin/article-pipeline")).rejects.toEqual(
      new ApiError(
        503,
        "Pipeline views are being prepared. Please retry shortly.",
        {},
        "projection_initializing",
      ),
    );
  });
  it("uses same-origin cookies and no cache", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ enabled: true }));
    vi.stubGlobal("fetch", fetcher);
    expect(await adminFetch("/v1/admin/auth/config")).toEqual({ enabled: true });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/auth/config",
      expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
    );
  });
  it("handles empty logout responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await adminFetch("/v1/admin/auth/logout", { method: "POST" })).toBeUndefined();
  });
  it("uses the generated logout endpoint with cookies and the csrf header", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    await adminAuthLogout({ headers: { "X-CSRF-Token": "csrf" } });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/auth/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "X-CSRF-Token": "csrf" },
      }),
    );
  });
  it("does not treat failed revocation as success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    await expect(adminAuthLogout()).rejects.toEqual(new ApiError(503));
  });
  it("replaces private page history with the sign-out confirmation", () => {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { origin: "https://admin.example", replace } });
    returnToLogin(true);
    expect(replace).toHaveBeenCalledWith("https://admin.example/login?signed_out=1");
  });
  it("does not claim a voluntary sign-out when a session simply expires", () => {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { origin: "https://admin.example", replace } });
    returnToLogin();
    expect(replace).toHaveBeenCalledWith("https://admin.example/login");
  });
  it("reports errors without echoing private upstream details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("private provider details", { status: 401 })),
    );
    await expect(adminFetch("/v1/admin/auth/me")).rejects.toEqual(new ApiError(401));
  });
});

it("allows worker registration names without allowing arbitrary proxy paths", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ name: "ai:host.1" }));
  vi.stubGlobal("fetch", fetcher);
  const response = await gateway(
    new Request("https://admin.example/api/v1/admin/workers/ai:host.1"),
    ["v1", "admin", "workers", "ai:host.1"],
  );
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://admin-api.internal:8001/v1/admin/workers/ai%3Ahost.1",
  );
  for (const name of ["..", ".", "evil?x", "evil#x", "a/../../auth", "%2e%2e", "a".repeat(257)]) {
    const rejected = await gateway(new Request("https://admin.example/api/test"), [
      "v1",
      "admin",
      "workers",
      name,
    ]);
    expect(rejected.status).toBe(404);
  }
  expect(fetcher).toHaveBeenCalledOnce();
});

it("relays provider logout without browser origin while isolating browser credentials", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const response = await gateway(
    new Request("https://admin.example/api", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Cookie: "private=session",
        "X-CSRF-Token": "private",
      },
      body: "logout_token=signed-token",
    }),
    ["v1", "admin", "auth", "backchannel-logout"],
  );
  expect(response.status).toBe(200);
  const headers = fetcher.mock.calls[0][1].headers;
  expect(headers.get("cookie")).toBeNull();
  expect(headers.get("origin")).toBeNull();
  expect(headers.get("x-csrf-token")).toBeNull();
  expect(
    (
      await gateway(new Request("https://admin.example/api", { method: "POST" }), [
        "v1",
        "admin",
        "auth",
        "sso",
        "logout",
      ])
    ).status,
  ).toBe(403);
});
