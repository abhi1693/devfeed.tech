import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/partner-tracking/events/route";

// Unit requests have no Next response lifecycle; reader-gateway.mjs covers HTTP completion.
vi.mock("next/server", () => ({ after: (callback: () => void) => callback() }));

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const request = (body = JSON.stringify({ receipt: "receipt", kind: "click" })) =>
  new Request("https://devfeed.tech/api/v1/partner-tracking/events?upstream=evil", {
    method: "POST",
    body,
    headers: { Cookie: "private", Authorization: "private", "X-CSRF-Token": "private" },
  });
beforeEach(() => {
  vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", undefined);
  vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", undefined);
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it("forwards only the bounded event body to the fixed collector without credentials", async () => {
  expect((await POST(request())).status).toBe(204);
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toBe("http://public-api:8000/v1/partner-tracking/events");
  expect(init?.headers).toEqual({ "Content-Type": "application/json" });
  expect(init).toMatchObject({ redirect: "manual", cache: "no-store" });
  expect(JSON.parse(new TextDecoder().decode(init!.body as ArrayBuffer))).toEqual({
    receipt: "receipt",
    kind: "click",
  });
});
it("does not forward browser caller claims when trusted edge forwarding is disabled", async () => {
  const incoming = request();
  incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
  incoming.headers.set("X-Forwarded-For", "203.0.113.7");
  expect((await POST(incoming)).status).toBe(204);
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
    "Content-Type": "application/json",
  });
});
it("replaces browser caller claims with a proof bound to the tracking destination", async () => {
  vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", "cf-connecting-ip");
  vi.stubEnv("DEVFEED_PARTNER_CALLER_KEY", "disposable-partner-caller-key-32-bytes");
  vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
  const incoming = request();
  incoming.headers.set("cf-connecting-ip", "203.0.113.7");
  incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
  incoming.headers.set("X-Forwarded-For", "198.51.100.1");
  expect((await POST(incoming)).status).toBe(204);
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
    "Content-Type": "application/json",
    "X-DevFeed-Partner-Caller":
      "v1.1800000000.203.0.113.7.261cbf16c4ee77a0e2a10c0d344a1190b143e1ba4c588e673b95cb2aa9735218",
  });
});
it.each(["missing-edge-ip", "invalid-edge-ip", "missing-key"])(
  "fails closed before event collection when configured caller trust is invalid (%s)",
  async (mode) => {
    vi.stubEnv("DEVFEED_PARTNER_CALLER_IP_HEADER", "cf-connecting-ip");
    vi.stubEnv(
      "DEVFEED_PARTNER_CALLER_KEY",
      mode === "missing-key" ? undefined : "disposable-partner-caller-key-32-bytes",
    );
    const incoming = request();
    incoming.headers.set("X-DevFeed-Partner-Caller", "forged-proof");
    if (mode !== "missing-edge-ip")
      incoming.headers.set(
        "cf-connecting-ip",
        mode === "invalid-edge-ip" ? "203.0.113.7, 198.51.100.1" : "203.0.113.7",
      );
    const response = await POST(incoming);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "Partner tracking unavailable" });
    expect(fetch).not.toHaveBeenCalled();
  },
);
it("rejects an oversized body before calling the collector", async () => {
  const response = await POST(request("x".repeat(2049)));
  expect(response.status).toBe(413);
  expect(await response.json()).toEqual({ detail: "Request too large" });
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(response.headers.get("connection")).toBe("close");
  expect(fetch).not.toHaveBeenCalled();
});
it.each([403, 429, 503, 302])(
  "keeps event errors private and preserves retry guidance (%s)",
  async (status) => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(null, { status, headers: { "Retry-After": "60" } }),
    );
    const response = await POST(request());
    expect(response.status).toBe(status >= 300 && status < 400 ? 503 : status);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("retry-after")).toBe("60");
  },
);
