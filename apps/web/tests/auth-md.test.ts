import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/auth.md/route";

vi.mock("server-only", () => ({}));
afterEach(() => vi.unstubAllEnvs());

it("documents existing OAuth registration with authoritative resource discovery and consent", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://reader.example");
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", "https://agents.example/custom/mcp");
  const response = GET();
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
  expect(response.headers.get("cache-control")).toBe("no-store");
  const body = await response.text();
  expect(body).toMatch(/^# .*auth\.md\n/);
  expect(body).toContain("https://agents.example/.well-known/oauth-protected-resource/custom/mcp");
  expect(body).toContain("registration_endpoint");
  expect(body).toContain('"token_endpoint_auth_method": "none"');
  expect(body).toContain("S256");
  expect(body).toContain("Authorization: Bearer <access_token>");
  expect(body).toContain("https://reader.example/legal/privacy");
  expect(body).toContain("are not implemented");
});

it("does not invent an MCP issuer or endpoints when unconfigured", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", "");
  const response = GET();
  const body = await response.text();
  expect(response.status).toBe(200);
  expect(body).toContain("No MCP resource URL is configured");
  expect(body).not.toContain("/.well-known/oauth-protected-resource");
});

it("fails closed without exposing invalid configuration", async () => {
  vi.stubEnv("DEVFEED_MCP_PUBLIC_URL", "https://user:secret@agents.example/mcp");
  const response = GET();
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("secret");
});
