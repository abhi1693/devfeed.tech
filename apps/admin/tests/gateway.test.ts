import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { gateway } from "@/lib/server/gateway";
import { adminFetch, ApiError, returnToLogin } from "@/lib/api/client";
import { adminAuthLogout } from "@/lib/api/generated/admin";

beforeEach(() => {
  vi.stubEnv("DEVFEED_ADMIN_BASE_URL", "https://admin.example");
  vi.stubEnv("DEVFEED_ADMIN_API_URL", "http://admin-api.internal:8001");
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("isolated API gateway", () => {
  it.each([
    ["POST", "sources", 120_000], ["POST", "sources/preview", 210_000],
    ["GET", "sources", 45_000], ["POST", "sources/source-1/fetch", 45_000],
    ["PATCH", "sources/source-1", 45_000], ["POST", "sources/preview-extra", 45_000],
  ])("uses the operation deadline for %s %s", async (method, resource, expected) => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
    await gateway(new Request(`https://admin.example/api/v1/admin/${resource}`, {
      method, headers: { Origin: "https://admin.example" },
    }), ["v1", "admin", ...resource.split("/")]);
    expect(timeout).toHaveBeenCalledWith(expected);
  });

  it.each([["sources", 70_000], ["sources/preview", 130_000]])("keeps a slow valid %s operation connected until its response", async (resource, delay) => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation(milliseconds => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), milliseconds);
      return controller.signal;
    });
    vi.stubGlobal("fetch", vi.fn((_input, init) => new Promise<Response>((resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
      setTimeout(() => resolve(Response.json({ id: "saved-source" }, { status: resource === "sources" ? 201 : 200 })), delay);
    })));
    const pending = gateway(new Request(`https://admin.example/api/v1/admin/${resource}`, {
      method: "POST", headers: { Origin: "https://admin.example" },
    }), ["v1", "admin", ...resource.split("/")]);
    await vi.advanceTimersByTimeAsync(delay);
    const response = await pending;
    expect(response.status).toBe(resource === "sources" ? 201 : 200);
    expect(await response.json()).toEqual({ id: "saved-source" });
  });

  it("still propagates client cancellation during long source validation", async () => {
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_input, init) => new Promise<Response>((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    })));
    const pending = gateway(new Request("https://admin.example/api/v1/admin/sources", {
      method: "POST", headers: { Origin: "https://admin.example" }, signal: controller.signal,
    }), ["v1", "admin", "sources"]);
    controller.abort();
    expect((await pending).status).toBe(503);
  });

  it("streams inbox hints and forwards ETags without exposing subscriber credentials", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('event: hint\ndata: {}\n\n', { headers: { "Content-Type": "text/event-stream", "X-Accel-Buffering": "no", "ETag": '"etag"' } }));
    vi.stubGlobal("fetch", fetcher);
    const request = new Request("https://admin.example/api/v1/admin/notifications/chimely/v1/inbox/stream", { headers: { "If-None-Match": '"old"', "Last-Event-ID": "resume", "X-Chimely-Subscriber-Hash": "forged" } });
    const result = await gateway(request, ["v1", "admin", "notifications", "chimely", "v1", "inbox", "stream"]);
    expect(result.headers.get("content-type")).toBe("text/event-stream");
    expect(result.headers.get("x-accel-buffering")).toBe("no");
    expect(result.headers.get("etag")).toBe('"etag"');
    expect(await result.text()).toContain("event: hint");
    expect(fetcher.mock.calls[0][1].headers.get("if-none-match")).toBe('"old"');
    expect(fetcher.mock.calls[0][1].headers.get("last-event-id")).toBe("resume");
    expect(fetcher.mock.calls[0][1].headers.get("x-chimely-subscriber-hash")).toBeNull();
  });
  it("preserves both callback cookies and redirects without following them", async () => {
    const upstream = new Response(null, { status: 302, headers: { Location: "https://admin.example/" } });
    upstream.headers.append("Set-Cookie", "__Host-devfeed_admin_session=opaque; Secure; HttpOnly; Path=/");
    upstream.headers.append("Set-Cookie", "__Host-devfeed_admin_state=; Max-Age=0; Secure; HttpOnly; Path=/");
    const fetcher = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/callback?state=s&code=c", {
      headers: { Cookie: "__Host-devfeed_admin_state=browser", Authorization: "Bearer untrusted", "X-Forwarded-Host": "evil.example" },
    }), ["v1", "admin", "auth", "callback"]);
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
    ["v1", "sources"], ["v1", "admin", "..", "health"],
    ["https:", "evil.example"], ["v1", "admin", "%2e%2e", "keys"],
  ])("rejects non-admin and traversal paths %j", async (...segments) => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const result = await gateway(new Request("https://admin.example/api/test"), segments);
    expect(result.status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([null, "https://evil.example"])("rejects writes with origin %s", async (origin) => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/logout", {
      method: "POST", headers: origin ? { Origin: origin } : {},
    }), ["v1", "admin", "auth", "logout"]);
    expect(result.status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards the csrf header and original origin for authorized writes", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/logout", {
      method: "POST", headers: { Origin: "https://admin.example", "X-CSRF-Token": "csrf" },
    }), ["v1", "admin", "auth", "logout"]);
    expect(result.status).toBe(204);
    expect(fetcher.mock.calls[0][1].headers.get("x-csrf-token")).toBe("csrf");
  });

  it("passes logout cookie deletions to the browser", async () => {
    const upstream = new Response(null, { status: 204 });
    upstream.headers.append("Set-Cookie", "__Host-devfeed_admin_session=; Max-Age=0; Secure; HttpOnly; Path=/");
    upstream.headers.append("Set-Cookie", "__Host-devfeed_admin_state=; Max-Age=0; Secure; HttpOnly; Path=/");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream));
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/logout", {
      method: "POST", headers: { Origin: "https://admin.example", "X-CSRF-Token": "csrf" },
    }), ["v1", "admin", "auth", "logout"]);
    expect(result.status).toBe(204);
    expect(result.headers.getSetCookie()).toEqual(upstream.headers.getSetCookie());
    expect(result.headers.get("cache-control")).toBe("no-store");
  });

  it("has no implicit upstream URL and does not expose errors", async () => {
    vi.stubEnv("DEVFEED_ADMIN_API_URL", "");
    const result = await gateway(new Request("https://admin.example/api/v1/admin/auth/me"), ["v1", "admin", "auth", "me"]);
    expect(result.status).toBe(503);
    expect(await result.json()).toEqual({ detail: "Admin service unavailable" });
  });
});

describe("Orval transport", () => {
  it("uses same-origin cookies and no cache", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ enabled: true }));
    vi.stubGlobal("fetch", fetcher);
    expect(await adminFetch("/v1/admin/auth/config")).toEqual({ enabled: true });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/auth/config", expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
  });
  it("handles empty logout responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await adminFetch("/v1/admin/auth/logout", { method: "POST" })).toBeUndefined();
  });
  it("uses the generated logout endpoint with cookies and the csrf header", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    await adminAuthLogout({ headers: { "X-CSRF-Token": "csrf" } });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/auth/logout", expect.objectContaining({
      method: "POST", credentials: "same-origin", cache: "no-store", headers: { "X-CSRF-Token": "csrf" },
    }));
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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private provider details", { status: 401 })));
    await expect(adminFetch("/v1/admin/auth/me")).rejects.toEqual(new ApiError(401));
  });
});
