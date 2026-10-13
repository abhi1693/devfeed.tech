import { beforeEach, expect, it, vi } from "vitest";
import { PortalPage } from "../src/lib/server/portal-page";
import { partnerAccount } from "./partner-fixtures";

const { session } = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("../src/lib/server/session", () => ({ portalSession: session }));
beforeEach(() => session.mockReset());
const account = partnerAccount();
function response(requestedAccount?: typeof account) {
  return { identity: {}, accounts: { items: [account], total: 205 }, requestedAccount };
}

it.each(["overview", "performance", "products", "connections", "settings"] as const)(
  "opens global %s without selecting or redirecting to the first account",
  async (section) => {
    session.mockResolvedValueOnce(response());
    const page = await PortalPage({ section, searchParams: Promise.resolve({}) });
    expect(page.props.section).toBe(section);
    expect(page.props.initialAccount).toBeUndefined();
    expect(page.props.initialAccounts.total).toBe(205);
    expect(session).toHaveBeenCalledWith(0, undefined, "", 25);
  },
);

it("authorizes a bookmarked settings record independently of the account list page", async () => {
  const bookmarked = partnerAccount({
    id: "22222222-2222-4222-8222-222222222222",
    name: "Off-page account",
  });
  session.mockResolvedValueOnce(response(bookmarked));
  const page = await PortalPage({
    section: "settings",
    accountId: bookmarked.id,
    searchParams: Promise.resolve({ account_offset: "100" }),
  });
  expect(page.props.initialAccount).toEqual(bookmarked);
  expect(page.props.initialAccounts.items).toEqual([account]);
  expect(page.props.initialAccountOffset).toBe(100);
  expect(session).toHaveBeenCalledWith(100, bookmarked.id, "", 25);
});

it("authorizes an explicit account policy filter before rendering a clean product route", async () => {
  session.mockResolvedValueOnce(response(account));
  const productId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const page = await PortalPage({
    section: "products",
    productRoute: { view: "placements", productId: productId.toUpperCase() },
    searchParams: Promise.resolve({ account_id: account.id, q: "cron", product_offset: "25" }),
  });
  expect(page.props.initialAccount).toEqual(account);
  expect(page.props.productRoute).toEqual({ view: "placements", productId });
  expect(page.props.initialProductSearch).toBe("cron");
  expect(page.props.initialProductOffset).toBe(25);
  expect(session).toHaveBeenCalledWith(0, account.id, "", 25);
});

it("keeps product bookmarks without an account filter open to every authorized policy", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "products",
    productRoute: { view: "detail", productId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
    searchParams: Promise.resolve({ policy_offset: "25" }),
  });
  expect(page.props.initialAccount).toBeUndefined();
  expect(page.props.initialPolicyOffset).toBe(25);
});

it("restores global account search and pagination without mounting account forms", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "settings",
    searchParams: Promise.resolve({ q: "Shipyard", account_offset: "25" }),
  });
  expect(page.props.initialAccountSearch).toBe("Shipyard");
  expect(page.props.initialAccountOffset).toBe(25);
  expect(page.props.initialAccount).toBeUndefined();
  expect(session).toHaveBeenCalledWith(25, undefined, "Shipyard", 25);
});

it("recovers settings pagination after account membership changes", async () => {
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [], total: 1 } });
  session.mockResolvedValueOnce({ identity: {}, accounts: { items: [account], total: 1 } });
  const page = await PortalPage({
    section: "settings",
    searchParams: Promise.resolve({ account_offset: "100" }),
  });
  expect(page.props.initialAccountOffset).toBe(0);
  expect(session).toHaveBeenCalledTimes(2);
});

it("restores server-owned reporting sort and pagination on global URLs", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "performance",
    searchParams: Promise.resolve({
      days: "7",
      sort: "account",
      order: "desc",
      asset_offset: "100",
      account_offset: "100",
    }),
  });
  expect(page.props).toMatchObject({
    initialDays: 7,
    initialSort: "account",
    initialOrder: "desc",
    initialAssetOffset: 100,
    initialAccountOffset: 0,
  });
  expect(session).toHaveBeenCalledWith(0, undefined, "", 25);
});

it("normalizes invalid and repeated non-authorization query values", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "products",
    searchParams: Promise.resolve({
      q: ["first", "second"],
      sort: "status",
      order: "sideways",
      product_offset: "-25",
      days: ["7", "30"],
    }),
  });
  expect(page.props).toMatchObject({
    initialDays: 30,
    initialSort: "name",
    initialOrder: "asc",
    initialProductOffset: 0,
    initialProductSearch: "",
  });
});

it.each(["invalid", [account.id, "22222222-2222-4222-8222-222222222222"]])(
  "rejects invalid or ambiguous account filters before authentication",
  async (account_id) => {
    await expect(
      PortalPage({ section: "products", searchParams: Promise.resolve({ account_id }) }),
    ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(session).not.toHaveBeenCalled();
  },
);

it("rejects a conflicting settings record and account filter", async () => {
  await expect(
    PortalPage({
      section: "settings",
      accountId: account.id,
      searchParams: Promise.resolve({ account_id: "22222222-2222-4222-8222-222222222222" }),
    }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
  expect(session).not.toHaveBeenCalled();
});

it("does not authorize a record from the currently listed account page", async () => {
  session.mockRejectedValueOnce(new Error("NEXT_HTTP_ERROR_FALLBACK;404"));
  await expect(
    PortalPage({ section: "settings", accountId: account.id, searchParams: Promise.resolve({}) }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
});

it.each(["metrics", "performance", "not-an-account"])(
  "rejects invalid resource IDs such as %s before authentication",
  async (accountId) => {
    await expect(
      PortalPage({ section: "settings", accountId, searchParams: Promise.resolve({}) }),
    ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(session).not.toHaveBeenCalled();
  },
);

it("normalizes real connection slugs without treating them as account UUIDs", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "connections",
    connectionsRoute: { view: "detail", connectionId: "NICK-LAUNCHES" },
    searchParams: Promise.resolve({}),
  });
  expect(page.props.connectionsRoute.connectionId).toBe("nick-launches");
  expect(page.props.initialAccount).toBeUndefined();
});

it("opens all connections without resolving an obsolete account filter", async () => {
  session.mockResolvedValueOnce(response());
  const page = await PortalPage({
    section: "connections",
    searchParams: Promise.resolve({ account_id: account.id }),
    connectionsRoute: { view: "list", q: "api", offset: 25 },
  });
  expect(page.props.initialAccount).toBeUndefined();
  expect(page.props.connectionsRoute).toEqual({ view: "list", q: "api", offset: 25 });
  expect(session).toHaveBeenCalledWith(0, undefined, "", 25);
});

it("rejects invalid connection slugs before authentication", async () => {
  await expect(
    PortalPage({
      section: "connections",
      connectionsRoute: { view: "detail", connectionId: "not/a/connection" },
      searchParams: Promise.resolve({}),
    }),
  ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
  expect(session).not.toHaveBeenCalled();
});

it("restores the archived product filter independently of membership pagination", async () => {
  session.mockResolvedValueOnce(response(account));
  const page = await PortalPage({
    section: "products",
    searchParams: Promise.resolve({
      include_archived: "true",
      q: "cron",
      product_offset: "25",
      account_id: account.id,
    }),
  });
  expect(page.props.initialIncludeArchived).toBe(true);
  expect(page.props.initialProductSearch).toBe("cron");
  expect(page.props.initialProductOffset).toBe(25);
  expect(page.props.initialAccount).toEqual(account);
});
