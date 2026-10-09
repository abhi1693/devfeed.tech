import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { portalSession } from "../src/lib/server/session";
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: (location: string) => {
    throw new Error(`redirect:${location}`);
  },
}));
beforeEach(() => vi.stubEnv("DEVFEED_PARTNER_API_URL", "http://partner-api:8004"));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("forwards only the host session and preserves account pagination", async () => {
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) =>
      name === "__Host-devfeed_partner_session" ? { name, value: "private" } : undefined,
  } as Awaited<ReturnType<typeof cookies>>);
  const identity = { subject: "alice" },
    accounts = { items: [], total: 0 };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(Response.json(identity))
    .mockResolvedValueOnce(Response.json(accounts));
  vi.stubGlobal("fetch", fetchMock);
  expect(await portalSession(25)).toEqual({ identity, accounts });
  expect(fetchMock.mock.calls[1][0]).toContain("accounts?offset=25");
  expect(fetchMock.mock.calls[0][1]).toMatchObject({
    headers: { Cookie: "__Host-devfeed_partner_session=private" },
    cache: "no-store",
    redirect: "error",
  });
});
it("redirects without calling the service when no session exists", async () => {
  vi.mocked(cookies).mockResolvedValue({ get: () => undefined } as Awaited<
    ReturnType<typeof cookies>
  >);
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  await expect(portalSession()).rejects.toThrow("redirect:/login");
  expect(fetchMock).not.toHaveBeenCalled();
});
it.each([401, 403, 503])("handles upstream session failure %s", async (status) => {
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) =>
      name === "devfeed_partner_session" ? { name, value: "private" } : undefined,
  } as Awaited<ReturnType<typeof cookies>>);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));
  await expect(portalSession()).rejects.toThrow(
    status === 503 ? "Partner service unavailable" : "redirect:/login?error=access_denied",
  );
});
