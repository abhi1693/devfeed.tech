import { afterEach, expect, it, vi } from "vitest";
import { gateway } from "../src/lib/server/gateway";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("cannot proxy admin, user or traversal paths", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  for (const segments of [
    ["v1", "admin", "accounts"],
    ["v1", "user", "auth", "me"],
    ["v1", "partner", "..", "accounts"],
  ]) {
    expect((await gateway(new Request("https://partner.example/api"), segments)).status).toBe(404);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});
it("rejects cross-origin mutations before calling the API", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  expect(
    (
      await gateway(
        new Request("https://partner.example/api", {
          method: "POST",
          headers: { Origin: "https://evil.example" },
        }),
        ["v1", "partner", "auth", "logout"],
      )
    ).status,
  ).toBe(403);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("rejects all partner reporting mutations even from its own origin", async () => {
  vi.stubEnv("DEVFEED_PARTNER_BASE_URL", "https://partner.example");
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const response = await gateway(
    new Request("https://partner.example/api", {
      method: "POST",
      headers: { Origin: "https://partner.example" },
    }),
    ["v1", "partner", "accounts"],
  );
  expect(response.status).toBe(405);
  expect(fetchMock).not.toHaveBeenCalled();
});
