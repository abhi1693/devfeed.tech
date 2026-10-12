import { beforeEach, expect, it, vi } from "vitest";
import { PortalPage } from "../src/lib/server/portal-page";
const { session } = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("../src/lib/server/session", () => ({ portalSession: session }));
beforeEach(() => session.mockReset());

it.each([0, 100])(
  "opens an authorized bookmarked account absent from picker offset %s",
  async (offset) => {
    const requestedAccount = {
      id: "22222222-2222-2222-2222-222222222222",
      name: "Bookmarked account",
      tier: "gold",
      status: "active",
      benefits: [],
    };
    const pickerPage = {
      items: Array.from({ length: 100 }, (_, index) => ({
        ...requestedAccount,
        id: `10000000-0000-4000-8000-${String(offset + index).padStart(12, "0")}`,
        name: `Other account ${offset + index}`,
      })),
      total: 205,
    };
    session.mockResolvedValueOnce({ identity: {}, accounts: pickerPage, requestedAccount });
    const page = await PortalPage({
      section: "performance",
      accountId: requestedAccount.id,
      searchParams: Promise.resolve({ account_offset: String(offset), days: "90" }),
    });
    expect(page.props.initialAccount).toEqual(requestedAccount);
    expect(page.props.initialAccountOffset).toBe(offset);
    expect(page.props.initialAccounts).toEqual(pickerPage);
    expect(page.props.initialDays).toBe(90);
    expect(session).toHaveBeenCalledTimes(1);
    expect(session).toHaveBeenCalledWith(offset, requestedAccount.id);
  },
);

it("keeps the bookmarked account selected after picker names reorder across page boundaries", async () => {
  const selected = { id: "22222222-2222-2222-2222-222222222222", name: "Renamed partner" };
  const previous = { items: [selected], total: 205 };
  const reordered = {
    items: [{ id: "11111111-1111-1111-1111-111111111111", name: "Another partner" }],
    total: 205,
  };
  session.mockResolvedValueOnce({ identity: {}, accounts: previous, requestedAccount: selected });
  session.mockResolvedValueOnce({ identity: {}, accounts: reordered, requestedAccount: selected });
  const route = {
    section: "assets" as const,
    accountId: selected.id,
    searchParams: Promise.resolve({ account_offset: "100", sort: "clicks", order: "desc" }),
  };
  const first = await PortalPage(route);
  const reopened = await PortalPage(route);
  expect(first.props.initialAccount).toEqual(selected);
  expect(reopened.props.initialAccount).toEqual(selected);
  expect(reopened.props.initialAccounts).toEqual(reordered);
  expect(reopened.props.initialSort).toBe("clicks");
  expect(reopened.props.initialOrder).toBe("desc");
});

it("uses the canonical account identity returned by the scoped lookup", async () => {
  const requestedAccount = {
    id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    name: "Canonical partner",
  };
  const routeId = requestedAccount.id.toUpperCase();
  session.mockResolvedValueOnce({
    identity: {},
    accounts: { items: [], total: 205 },
    requestedAccount,
  });
  const page = await PortalPage({
    section: "overview",
    accountId: routeId,
    searchParams: Promise.resolve({}),
  });
  expect(session).toHaveBeenCalledWith(0, routeId);
  expect(page.props.initialAccount).toEqual(requestedAccount);
});

it("recovers a sole account from an old account pagination URL", async () => {
  const account = {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Alpha",
    tier: "gold",
    status: "active",
    benefits: [],
  };
  const identity = { subject: "partner", roles: ["partner"] };
  session.mockResolvedValueOnce({
    identity,
    accounts: { items: [], total: 1 },
    requestedAccount: account,
  });
  session.mockResolvedValueOnce({
    identity,
    accounts: { items: [account], total: 1 },
    requestedAccount: account,
  });
  const page = await PortalPage({
    section: "overview",
    accountId: "11111111-1111-1111-1111-111111111111",
    searchParams: Promise.resolve({ account_offset: "100" }),
  });
  expect(session).toHaveBeenNthCalledWith(1, 100, account.id);
  expect(session).toHaveBeenNthCalledWith(2, 0, account.id);
  expect(page.props.initialAccountOffset).toBe(0);
  expect(page.props.initialAccount).toEqual(account);
  expect(page.props.initialAccounts.items).toEqual([account]);
});

it("rejects an account path outside the current membership", async () => {
  session.mockRejectedValueOnce(new Error("NEXT_HTTP_ERROR_FALLBACK;404"));
  await expect(
    PortalPage({
      section: "performance",
      accountId: "22222222-2222-2222-2222-222222222222",
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
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
  session.mockResolvedValueOnce({
    identity: {},
    accounts: { items: [account], total: 1 },
    requestedAccount: account,
  });
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
  session.mockResolvedValueOnce({
    identity: {},
    accounts: { items: [account], total: 1 },
    requestedAccount: account,
  });
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

it("opens the first account on the requested picker page when no account path was supplied", async () => {
  const accountId = "11111111-1111-1111-1111-111111111111";
  session.mockResolvedValueOnce({
    identity: {},
    accounts: { items: [{ id: accountId }], total: 205 },
  });
  await expect(
    PortalPage({
      section: "performance",
      searchParams: Promise.resolve({ account_offset: "100", days: "90" }),
    }),
  ).rejects.toMatchObject({
    digest: expect.stringContaining(
      `/${accountId}/performance?days=90&sort=name&order=asc&account_offset=100`,
    ),
  });
});

it("renders an empty membership state when no account path or memberships exist", async () => {
  const accounts = { items: [], total: 0 };
  session.mockResolvedValueOnce({ identity: {}, accounts });
  const page = await PortalPage({
    section: "overview",
    searchParams: Promise.resolve({}),
  });
  expect(page.props.initialAccount).toBeUndefined();
  expect(page.props.initialAccounts).toEqual(accounts);
});
