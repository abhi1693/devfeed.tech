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
    searchParams: Promise.resolve({ account_offset: "100", account: "alpha" }),
  });
  expect(session).toHaveBeenNthCalledWith(1, 100);
  expect(session).toHaveBeenNthCalledWith(2, 0);
  expect(page.props.initialAccountOffset).toBe(0);
  expect(page.props.initialSelected).toBe("alpha");
  expect(page.props.initialAccounts.items).toEqual([account]);
});
