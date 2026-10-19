import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/.well-known/openid-configuration/route";

vi.mock("server-only", () => ({}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("publishes the real provider's JSON without forwarding reader credentials", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://private-user-api:8002");
  const metadata = {
    issuer: "https://identity.example/tenant",
    authorization_endpoint: "https://identity.example/authorize",
    token_endpoint: "https://identity.example/token",
    jwks_uri: "https://identity.example/keys",
    grant_types_supported: ["authorization_code"],
    response_types_supported: ["code"],
  };
  const fetcher = vi.fn().mockResolvedValue(Response.json(metadata));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request("https://devfeed.tech/.well-known/openid-configuration", {
      headers: { Cookie: "session=private", Authorization: "Bearer secret" },
    }),
  );
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toContain("application/json");
  expect(response.headers.get("content-location")).toBe(
    "https://identity.example/tenant/.well-known/openid-configuration",
  );
  expect(await response.json()).toEqual(metadata);
  const [url, options] = fetcher.mock.calls[0];
  expect(url.href).toBe("http://private-user-api:8002/.well-known/openid-configuration");
  expect(options.headers).not.toHaveProperty("Cookie");
  expect(options.headers).not.toHaveProperty("Authorization");
  expect(options.redirect).toBe("error");
});

it.each([404, 500])("returns a controlled response for upstream %s", async (status) => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://private-user-api:8002");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private details", { status })));
  const response = await GET(new Request("https://devfeed.tech/.well-known/openid-configuration"));
  expect(response.status).toBe(status === 404 ? 404 : 503);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.text()).not.toContain("private details");
});
