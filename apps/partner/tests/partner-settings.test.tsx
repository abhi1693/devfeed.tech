// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerSettings } from "../src/app/partner-settings";
import { partnerAccount } from "./partner-fixtures";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const alpha = partnerAccount({ name: "Alpha" });
const beta = partnerAccount({ id: "22222222-2222-4222-8222-222222222222", name: "Beta" });
const unavailable = vi.fn();
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function settings() {
  return render(
    <PartnerSettings
      initialAccounts={{ items: [alpha], total: 205 }}
      onUnavailable={unavailable}
    />,
  );
}

it("shows authorized account records without selectors or mounted account forms", () => {
  vi.stubGlobal("fetch", vi.fn());
  settings();
  const table = screen.getByRole("table", { name: "Partner accounts" });
  expect(within(table).getByRole("link", { name: "Alpha" }).getAttribute("href")).toBe(
    `/settings/${alpha.id}`,
  );
  expect(screen.getByText("1–25 of 205")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save click destination" })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("paginates all account settings and reloads the first page when returning", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      Response.json({ items: url.includes("offset=25") ? [beta] : [alpha], total: 205 }),
    );
  vi.stubGlobal("fetch", fetcher);
  settings();
  await userEvent.click(screen.getByRole("button", { name: "Next accounts" }));
  await screen.findByRole("link", { name: "Beta" });
  expect(fetcher).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts?offset=25&limit=25&q=",
    expect.anything(),
  );
  expect(window.location.pathname).toBe("/settings");
  expect(new URLSearchParams(window.location.search).get("account_offset")).toBe("25");
  await userEvent.click(screen.getByRole("button", { name: "Previous accounts" }));
  await screen.findByRole("link", { name: "Alpha" });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(new URLSearchParams(window.location.search).has("account_offset")).toBe(false);
});

it("searches every authorized account remotely and resets pagination", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json({ items: [beta], total: 1 }));
  vi.stubGlobal("fetch", fetcher);
  settings();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search partner accounts" }), {
    target: { value: "Beta" },
  });
  await screen.findByRole("link", { name: "Beta" });
  expect(fetcher).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts?offset=0&limit=25&q=Beta",
    expect.anything(),
  );
  expect(screen.getByRole("link", { name: "Beta" }).getAttribute("href")).toBe(
    `/settings/${beta.id}?q=Beta`,
  );
  expect(screen.queryByRole("link", { name: "Alpha" })).toBeNull();
});

it("rejects malformed refreshes and removes stale records until retry succeeds", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ items: [{ id: alpha.id }], total: 1 }))
    .mockResolvedValueOnce(Response.json({ items: [alpha], total: 205 }));
  vi.stubGlobal("fetch", fetcher);
  settings();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("link", { name: "Alpha" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Retry partner accounts" }));
  await screen.findByRole("link", { name: "Alpha" });
});

it("refreshes on demand while retaining the account-list URL and no global selection", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [alpha, beta], total: 205 }));
  vi.stubGlobal("fetch", fetcher);
  settings();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("link", { name: "Beta" });
  await waitFor(() => expect(screen.queryByText("Loading partner accounts…")).toBeNull());
  expect(new URLSearchParams(window.location.search).has("account_id")).toBe(false);
});
