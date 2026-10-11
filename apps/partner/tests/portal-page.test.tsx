import { expect, it, vi } from "vitest";
import { PortalPage } from "../src/lib/server/portal-page";
const { session } = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("../src/lib/server/session", () => ({ portalSession: session }));

it("recovers a sole account from an old account pagination URL", async () => {
  const account = {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Alpha",
    tier: "gold",
    status: "active",
    benefits: [],
  };
  const identity = { subject: "partner", roles: ["partner"] };
  session.mockResolvedValueOnce({ identity, accounts: { items: [], total: 1 } });
  session.mockResolvedValueOnce({ identity, accounts: { items: [account], total: 1 } });
  const page = await PortalPage({
    section: "overview",
    accountId: "11111111-1111-1111-1111-111111111111",
    searchParams: Promise.resolve({ account_offset: "100" }),
  });
  expect(session).toHaveBeenNthCalledWith(1, 100);
  expect(session).toHaveBeenNthCalledWith(2, 0);
  expect(page.props.initialAccountOffset).toBe(0);
  expect(page.props.initialSelected).toBe("11111111-1111-1111-1111-111111111111");
  expect(page.props.initialAccounts.items).toEqual([account]);
});

it("rejects an account path outside the current membership", async () => {
  session.mockResolvedValueOnce({
    identity: {},
    accounts: { items: [{ id: "11111111-1111-1111-1111-111111111111" }], total: 1 },
  });
  await expect(
    PortalPage({
      section: "performance",
      accountId: "22222222-2222-2222-2222-222222222222",
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow();
});

it("rejects the removed account query parameter", async () => {
  await expect(
    PortalPage({
      section: "overview",
      searchParams: Promise.resolve({ days: "7", account: "11111111-1111-1111-1111-111111111111" }),
    }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
});

it.each(["metrics", "performance", "assets", "not-an-account"])(
  "returns 404 for /%s before looking up a session",
  async (accountId) => {
    session.mockClear();
    await expect(
      PortalPage({ section: "overview", accountId, searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(session).not.toHaveBeenCalled();
  },
);

it("restores reporting sort, direction and asset pagination from the URL", async () => {
  const account = { id: "11111111-1111-1111-1111-111111111111" };
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [account], total: 1 } });
  const page = await PortalPage({
    section: "assets",
    accountId: account.id,
    searchParams: Promise.resolve({
      days: "7",
      sort: "impressions",
      order: "desc",
      asset_offset: "100",
    }),
  });
  expect(page.props).toMatchObject({
    initialDays: 7,
    initialSort: "impressions",
    initialOrder: "desc",
    initialAssetOffset: 100,
  });
});

it("normalizes invalid sorting and asset pagination parameters", async () => {
  const account = { id: "11111111-1111-1111-1111-111111111111" };
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [account], total: 1 } });
  const page = await PortalPage({
    section: "assets",
    accountId: account.id,
    searchParams: Promise.resolve({ sort: "status", order: "sideways", asset_offset: "-100" }),
  });
  expect(page.props).toMatchObject({
    initialSort: "name",
    initialOrder: "asc",
    initialAssetOffset: 0,
  });
});

it("preserves sorting and asset pagination when opening the default account", async () => {
  const account = { id: "11111111-1111-1111-1111-111111111111" };
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [account], total: 1 } });
  await expect(
    PortalPage({
      section: "assets",
      searchParams: Promise.resolve({ days: "7", sort: "ctr", order: "desc", asset_offset: "100" }),
    }),
  ).rejects.toMatchObject({
    digest: expect.stringContaining(
      `/${account.id}/assets?days=7&sort=ctr&order=desc&asset_offset=100`,
    ),
  });
});
