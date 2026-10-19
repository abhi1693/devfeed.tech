import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/.well-known/oauth-protected-resource/route";

vi.mock("server-only", () => ({}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each(["/custom/mcp", "/custom/mcp/", "/"])(
  "publishes real resource metadata and canonical discovery for %s without forwarding credentials",
  async (path) => {
    vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
    const metadata = {
      resource: `https://mcp.example${path}`,
      authorization_servers: ["https://authorization.example"],
      bearer_methods_supported: ["header"],
    };
    const fetcher = vi.fn().mockResolvedValue(Response.json(metadata));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(
      new Request("https://devfeed.tech/.well-known/oauth-protected-resource", {
        headers: { Cookie: "session=secret", Authorization: "Bearer private" },
      }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(response.headers.get("content-location")).toBe(
      `https://mcp.example/.well-known/oauth-protected-resource${path === "/" ? "" : path}`,
    );
    expect(await response.json()).toEqual(metadata);
    const [url, options] = fetcher.mock.calls[0];
    expect(url.href).toBe("http://user-api:8002/.well-known/oauth-protected-resource");
    expect(options.headers).not.toHaveProperty("Cookie");
    expect(options.headers).not.toHaveProperty("Authorization");
    expect(options.redirect).toBe("error");
  },
);

it.each([404, 500])("returns a safe non-cacheable error for upstream %s", async (status) => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private details", { status })));
  const response = await GET(
    new Request("https://devfeed.tech/.well-known/oauth-protected-resource"),
  );
  expect(response.status).toBe(status === 404 ? 404 : 503);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.text()).not.toContain("private details");
});
