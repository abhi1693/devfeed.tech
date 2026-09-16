import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/v1/extension/analytics/route";
import { extensionPage, extensionPayload } from "@/lib/extension-analytics";

const origin = "chrome-extension://hliakjocndflpkmfajndigbpngfcekdm";
const event = { name: "page_view", params: { page_path: "/search" } };
const body = () => ({
  client_id: `${crypto.getRandomValues(new Uint32Array(1))[0]}.1789380000`,
  session_id: 123456789,
  engagement_time_msec: 1000,
  extension_version: "0.1.0",
  event,
});
const request = (value: unknown = body(), source = origin) =>
  new Request("https://devfeed.tech/api/v1/extension/analytics", {
    method: "POST",
    headers: {
      Origin: source,
      "Content-Type": "application/json",
      Cookie: "private-session",
      Authorization: "private-token",
    },
    body: JSON.stringify(value),
  });
beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_EXTENSION_IDS", JSON.stringify([origin.split("//")[1]]));
  vi.stubEnv("DEVFEED_EXTENSION_ANALYTICS_ENABLED", "true");
  vi.stubEnv("DEVFEED_EXTENSION_GA_MEASUREMENT_ID", "G-EXTENSION");
  vi.stubEnv("DEVFEED_EXTENSION_GA_API_SECRET", "test-server-secret");
  vi.stubEnv("GOOGLE_ANALYTICS_ID", "G-WEBSITE");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it("exposes only enabled status and keeps the website property separate", async () => {
  expect(await GET(request()).json()).toEqual({ enabled: true });
  for (const value of ["false", ""]) {
    vi.stubEnv("DEVFEED_EXTENSION_ANALYTICS_ENABLED", value);
    expect(await GET(request()).json()).toEqual({ enabled: false });
    expect((await POST(request())).status).toBe(204);
  }
  vi.stubEnv("DEVFEED_EXTENSION_ANALYTICS_ENABLED", "true");
  vi.stubEnv("DEVFEED_EXTENSION_GA_MEASUREMENT_ID", "G-WEBSITE");
  expect(await GET(request()).json()).toEqual({ enabled: false });
  expect((await POST(request())).status).toBe(204);
  expect(fetch).not.toHaveBeenCalled();
});
it("rejects website, unconfigured extension, and missing origins", async () => {
  for (const value of ["https://devfeed.tech", origin + ".evil", "null", ""]) {
    expect((await POST(request(body(), value))).status).toBe(403);
    expect(await GET(request(body(), value)).json()).toEqual({ enabled: false });
  }
  expect(fetch).not.toHaveBeenCalled();
});
it("relays only sanitized events with server credentials, never cookies or identity fields", async () => {
  const input = {
    ...body(),
    email: "secret@example.test",
    user_id: "account-id",
    measurement_id: "G-WEBSITE",
  };
  expect((await POST(request(input))).status).toBe(204);
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  const target = new URL(String(url));
  expect(target.origin + target.pathname).toBe("https://www.google-analytics.com/mp/collect");
  expect(target.searchParams.get("measurement_id")).toBe("G-EXTENSION");
  expect(target.searchParams.get("api_secret")).toBe("test-server-secret");
  expect(init?.headers).toEqual({ "Content-Type": "application/json" });
  const payload = JSON.parse(String(init?.body));
  expect(payload).toEqual(extensionPayload(input));
  expect(payload.events[0].params.page_location).toBe("https://extension.devfeed.tech/search");
  expect(JSON.stringify(payload)).not.toMatch(
    /secret@example|account-id|G-WEBSITE|private-session|private-token/,
  );
});
it("rejects arbitrary events, text, invalid IDs and oversized bodies", async () => {
  for (const value of [
    { ...body(), event: { name: "arbitrary", params: {} } },
    { ...body(), event: { name: "page_view", params: { page_path: "/search?q=private" } } },
    { ...body(), event: { name: "page_view", params: { page_path: "/", email: "private" } } },
    { ...body(), client_id: "email@example.test" },
    { ...body(), engagement_time_msec: -1 },
    { ...body(), padding: "x".repeat(5000) },
  ])
    expect((await POST(request(value))).status).toBe(400);
  expect(fetch).not.toHaveBeenCalled();
  expect(extensionPage("/search?q=private@example.test")).toBe("/search");
  expect(extensionPage("/articles/some-public-article")).toBe("/articles");
});
it("bounds client traffic and sanitizes upstream failures", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("URL includes private secret"));
  const failed = await POST(request());
  expect(failed.status).toBe(502);
  expect(await failed.text()).toBe("");
  const input = body();
  for (let i = 0; i < 120; i++) expect((await POST(request(input))).status).toBe(204);
  expect((await POST(request(input))).status).toBe(429);
});

it("preserves browser attribution and supports installed clients without the new field", () => {
  for (const platform of ["chrome_extension", "edge_extension"] as const) {
    expect(
      extensionPayload({ ...body(), client_platform: platform })?.events[0].params.client_platform,
    ).toBe(platform);
  }
  expect(extensionPayload(body())?.events[0].params.client_platform).toBe("chrome_extension");
  expect(extensionPayload({ ...body(), client_platform: "arbitrary-browser" })).toBeNull();
});
