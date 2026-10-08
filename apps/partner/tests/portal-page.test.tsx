import { expect, it, vi } from "vitest";
import { PortalPage } from "../src/lib/server/portal-page";
const { session } = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("../src/lib/server/session", () => ({ portalSession: session }));

it("recovers a sole account from an old account pagination URL", async () => {
  const account = { id: "alpha", name: "Alpha", tier: "gold", status: "active", benefits: [] };
  const identity = { subject: "partner", roles: ["partner"] };
  session.mockResolvedValueOnce({ identity, accounts: { items: [], total: 1 } });
  session.mockResolvedValueOnce({ identity, accounts: { items: [account], total: 1 } });
  const page = await PortalPage({
    section: "overview",
    accountId: "alpha",
    searchParams: Promise.resolve({ account_offset: "100" }),
  });
  expect(session).toHaveBeenNthCalledWith(1, 100);
  expect(session).toHaveBeenNthCalledWith(2, 0);
  expect(page.props.initialAccountOffset).toBe(0);
  expect(page.props.initialSelected).toBe("alpha");
  expect(page.props.initialAccounts.items).toEqual([account]);
});

it("rejects an account path outside the current membership", async () => {
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [{ id: "alpha" }], total: 1 } });
  await expect(
    PortalPage({ section: "performance", accountId: "foreign", searchParams: Promise.resolve({}) }),
  ).rejects.toThrow();
});

it("rejects the removed account query parameter", async () => {
  await expect(
    PortalPage({
      section: "overview",
      searchParams: Promise.resolve({ days: "7", account: "alpha" }),
    }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
});
