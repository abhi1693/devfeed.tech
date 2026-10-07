import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/v1/partner-tracking/events/route";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
const request = (body = JSON.stringify({ receipt: "receipt", kind: "click" })) =>
  new Request("https://devfeed.tech/api/v1/partner-tracking/events?upstream=evil", {
    method: "POST",
    body,
    headers: { Cookie: "private", Authorization: "private", "X-CSRF-Token": "private" },
  });
beforeEach(() => {
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
it("rejects an oversized body before calling the collector", async () => {
  expect((await POST(request("x".repeat(2049)))).status).toBe(413);
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
