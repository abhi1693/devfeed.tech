import { afterEach, expect, it, vi } from "vitest";
import { GET as catalog } from "@/app/.well-known/api-catalog/route";
import { GET as description } from "@/app/api/openapi.json/route";
import { GET as documentation } from "@/app/api/docs/route";

vi.mock("server-only", () => ({}));
afterEach(() => vi.unstubAllEnvs());

it("publishes an RFC 9727 linkset with reachable reader description and documentation routes", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://reader.example");
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://internal-api:8000");
  const response = catalog();
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toBe(
    'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"',
  );
  expect(response.headers.get("link")).toBe(
    '<https://reader.example/.well-known/api-catalog>; rel="api-catalog"',
  );
  const body = await response.json();
  const api = body.linkset[0];
  expect(api.anchor).toBe("https://reader.example/api/v1");
  expect(api["service-desc"][0].href).toBe("https://reader.example/api/openapi.json");
  expect(api["service-doc"][0].href).toBe("https://reader.example/api/docs");
  expect(JSON.stringify(body)).not.toMatch(/internal|admin|health|localhost/);

  const spec = await description().json();
  expect(spec.openapi).toMatch(/^3\./);
  expect(spec.servers).toEqual([{ url: "https://reader.example" }]);
  expect(spec.paths["/api/v1/feed"].get).toBeDefined();
  expect(spec.paths["/api/v1/user/bookmarks"].get.security).toBeDefined();
  expect(Object.keys(spec.paths).every((path) => path.startsWith("/api/v1/"))).toBe(true);
  expect(await documentation().text()).toContain("https://reader.example/api/openapi.json");
});
