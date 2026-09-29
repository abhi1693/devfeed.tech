import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/mcp/config/route";
import { mcpPublicUrl } from "@/lib/server/config";

afterEach(() => vi.unstubAllEnvs());

it("reads the complete ingress URL at runtime without caching or rebuilding", async () => {
  for (const url of [
    "https://mcp.ingress.test/devfeed/mcp",
    "https://another-ingress.test/agents/devfeed",
  ]) {
    vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", url);
    expect(mcpPublicUrl()).toBe(url);
    const response = GET();
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({ url });
  }
});

it.each([undefined, "", "   "])(
  "advertises no guessed endpoint when the setting is %s",
  async (value) => {
    vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", value);
    expect(mcpPublicUrl()).toBeNull();
    expect(await GET().json()).toEqual({ url: null });
  },
);

it.each([
  "not a URL",
  "file:///mcp",
  "https://user:secret@mcp.test/mcp",
  "https://mcp.test/mcp#fragment",
])("rejects invalid public configuration without exposing it: %s", async (value) => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", value);
  expect(mcpPublicUrl).toThrow(/DEVFEED_MCP_PUBLIC_URL/);
  const response = GET();
  expect(response.status).toBe(503);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(await response.json()).toEqual({ detail: "MCP configuration is unavailable" });
});
