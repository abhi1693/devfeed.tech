import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { portalSession } from "../src/lib/server/session";
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_HTTP_ERROR_FALLBACK;404");
  },
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
  expect(await portalSession(25)).toEqual({ identity, accounts, requestedAccount: undefined });
  expect(fetchMock).toHaveBeenCalledTimes(2);
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

it("authorizes an off-page account with one scoped lookup and preserves the bounded picker page", async () => {
  const accountId = "22222222-2222-2222-2222-222222222222";
  const requestedAccount = { id: accountId, name: "Bookmarked partner" };
  const accounts = {
    items: [{ id: "11111111-1111-1111-1111-111111111111", name: "Other partner" }],
    total: 205,
  };
  const identity = { subject: "alice" };
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) =>
      name === "__Host-devfeed_partner_session" ? { name, value: "private" } : undefined,
  } as Awaited<ReturnType<typeof cookies>>);
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
    const endpoint = new URL(url);
    if (endpoint.pathname.endsWith("/auth/me")) return Response.json(identity);
    if (endpoint.pathname === "/v1/partner/accounts") {
      expect(endpoint.searchParams.get("offset")).toBe("100");
      return Response.json(accounts);
    }
    if (endpoint.pathname === `/v1/partner/accounts/${accountId}`)
      return Response.json(requestedAccount);
    throw new Error(`Unexpected private endpoint ${endpoint.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  const result = await portalSession(100, accountId);
  expect(result).toEqual({ identity, accounts, requestedAccount });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  for (const [, init] of fetchMock.mock.calls) {
    expect(init).toMatchObject({
      headers: { Cookie: "__Host-devfeed_partner_session=private" },
      cache: "no-store",
      redirect: "error",
    });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  }
});

it.each([401, 403, 404, 503])(
  "uses scoped account authorization even when the picker lists it (response %s)",
  async (status) => {
    const accountId = "22222222-2222-2222-2222-222222222222";
    vi.mocked(cookies).mockResolvedValue({
      get: (name: string) =>
        name === "devfeed_partner_session" ? { name, value: "private" } : undefined,
    } as Awaited<ReturnType<typeof cookies>>);
    const fetchMock = vi.fn(async (url: string) => {
      const endpoint = new URL(url);
      if (endpoint.pathname.endsWith("/auth/me")) return Response.json({ subject: "alice" });
      if (endpoint.pathname === "/v1/partner/accounts")
        return Response.json({ items: [{ id: accountId }], total: 1 });
      if (endpoint.pathname === `/v1/partner/accounts/${accountId}`)
        return new Response(null, { status });
      throw new Error(`Unexpected private endpoint ${endpoint.pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(portalSession(0, accountId)).rejects.toThrow(
      status === 404
        ? "NEXT_HTTP_ERROR_FALLBACK;404"
        : status === 503
          ? "Partner service unavailable"
          : "redirect:/login?error=access_denied",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `http://partner-api:8004/v1/partner/accounts/${accountId}`,
      expect.objectContaining({ cache: "no-store" }),
    );
  },
);
