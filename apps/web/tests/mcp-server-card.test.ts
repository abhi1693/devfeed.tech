import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET } from "@/app/.well-known/mcp/server-card.json/route";
import { GET as publicConfig } from "@/app/api/v1/mcp/config/route";

vi.mock("server-only", () => ({}));
beforeEach(() => vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", ""));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
const endpoint = "https://mcp.example/custom/mcp";
const card = {
  serverInfo: { name: "DevFeed", version: "9.8.7" },
  endpoint,
  capabilities: { tools: { listChanged: false } },
};
const request = () =>
  new Request("https://devfeed.tech/.well-known/mcp/server-card.json", {
    headers: { Cookie: "session=private", Authorization: "Bearer secret" },
  });

it("mirrors the actual MCP version and capabilities using a fixed configured endpoint", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  const fetcher = vi.fn().mockResolvedValue(Response.json(card));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual(card);
  expect(response.headers.get("content-location")).toBe(endpoint + "/server-card");
  expect(response.headers.get("access-control-allow-origin")).toBe("*");
  const [url, options] = fetcher.mock.calls[0];
  expect(url.href).toBe(endpoint + "/server-card");
  expect(options.headers).toEqual({ Accept: "application/mcp-server-card+json, application/json" });
  expect(options.redirect).toBe("error");
});

it.each(["http://mcp:8003/mcp", "http://mcp:8003/custom/mcp/"])(
  "fetches the private transport %s while publishing only the configured public identity",
  async (internal) => {
    vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
    vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", internal);
    const fetcher = vi.fn().mockResolvedValue(Response.json(card));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(request());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(card);
    expect(response.headers.get("content-location")).toBe(endpoint + "/server-card");
    expect(response.headers.get("link")).toBe(`<${endpoint}/server-card>; rel="canonical"`);
    expect(await publicConfig().json()).toEqual({ url: endpoint });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, options] = fetcher.mock.calls[0];
    expect(url.href).toBe(internal.replace(/\/$/, "") + "/server-card");
    expect(options.headers).toEqual({
      Accept: "application/mcp-server-card+json, application/json",
    });
    expect(options.redirect).toBe("error");
    expect(options.cache).toBe("no-store");
  },
);

it("checks a privately fetched card against its public endpoint", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", "http://mcp:8003/mcp");
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ ...card, endpoint: "http://mcp:8003/mcp" }));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("mcp:8003");
});

it("does not fabricate a card or fall back when the configured private MCP is unavailable", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", "http://mcp:8003/mcp");
  const fetcher = vi.fn().mockRejectedValue(new Error("private upstream connection failed"));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.json()).toEqual({ detail: "MCP server card is unavailable" });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  "file:///mcp",
  "https://user:secret@mcp.internal/mcp",
  "http://mcp:8003/mcp?token=secret",
  "http://mcp:8003/mcp#fragment",
])("rejects unsafe private configuration without fetching: %s", async (internal) => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", internal);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(fetcher).not.toHaveBeenCalled();
  expect(await response.json()).toEqual({ detail: "MCP server card is unavailable" });
});

it.each([
  { ...card, endpoint: "https://other.example/mcp" },
  { ...card, serverInfo: { name: "DevFeed" } },
  { ...card, capabilities: [] },
  { ...card, serverInfo: { name: "", version: "1" } },
])("rejects invalid upstream cards: %j", async (body) => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(body)));
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("bounds upstream bodies and handles HTTP errors", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", endpoint);
  for (const response of [
    new Response("x".repeat(65_537)),
    new Response("private", { status: 500 }),
  ]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const result = await GET(request());
    expect(result.status).toBe(503);
    expect(await result.text()).toBe('{"detail":"MCP server card is unavailable"}');
  }
});

it.each([
  "http://remote.example/mcp",
  "https://user:secret@mcp.example/mcp",
  "https://mcp.example/mcp?token=secret",
])("rejects unsafe public configuration without fetching: %s", async (url) => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", url);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(fetcher).not.toHaveBeenCalled();
  expect(await response.text()).not.toContain("secret");
});

it("does not advertise an unconfigured server", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", "");
  vi.stubEnv("DEVFEED_MCP_INTERNAL_URL", "http://mcp:8003/mcp");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(request());
  expect(response.status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});
