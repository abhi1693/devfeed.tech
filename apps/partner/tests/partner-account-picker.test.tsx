// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerAccountPicker } from "../src/app/partner-account-picker";
import { partnerAccount } from "./partner-fixtures";
import type { Account } from "../src/lib/types";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const unavailable = vi.fn();
const accounts = Array.from({ length: 105 }, (_, index) =>
  partnerAccount({
    id: `55555555-5555-4555-8555-${String(index + 1).padStart(12, "0")}`,
    name: `Account ${String(index).padStart(3, "0")}`,
  }),
);
function Harness() {
  const [account, setAccount] = useState<Account>();
  return (
    <PartnerAccountPicker
      account={account}
      onChange={setAccount}
      onUnavailable={unavailable}
      required
    />
  );
}
function serveAccounts() {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = new URL(String(input), "http://localhost");
    const offset = Number(url.searchParams.get("offset"));
    const limit = Number(url.searchParams.get("limit"));
    const q = url.searchParams.get("q") ?? "";
    const visible = accounts.filter((account) => account.name.includes(q));
    return Response.json({ items: visible.slice(offset, offset + limit), total: visible.length });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("starts without a selected account and reaches memberships beyond the first100", async () => {
  const fetcher = serveAccounts();
  render(<Harness />);
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole("combobox", { name: "Partner account" }).textContent).toContain(
    "Choose a partner account",
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await screen.findByRole("option", { name: "Account 000" });
  for (let offset = 25; offset <= 100; offset += 25) {
    fireEvent.click(screen.getByRole("button", { name: "Next accounts" }));
    await screen.findByRole("option", { name: `Account ${String(offset).padStart(3, "0")}` });
  }
  fireEvent.click(screen.getByRole("option", { name: "Account 104" }));
  expect(screen.getByRole("combobox", { name: "Partner account" }).textContent).toContain(
    "Account 104",
  );
  expect(
    fetcher.mock.calls.every(
      ([input]) => new URL(String(input), "http://localhost").searchParams.get("limit") === "25",
    ),
  ).toBe(true);
});

it("searches authorized accounts on the server without retaining a page boundary", async () => {
  const fetcher = serveAccounts();
  render(<Harness />);
  fireEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await screen.findByRole("option", { name: "Account 000" });
  fireEvent.change(screen.getByPlaceholderText("Search partner account…"), {
    target: { value: "Account 104" },
  });
  await screen.findByRole("option", { name: "Account 104" });
  const last = new URL(String(fetcher.mock.calls.at(-1)![0]), "http://localhost");
  expect(last.searchParams.get("q")).toBe("Account 104");
  expect(last.searchParams.get("offset")).toBe("0");
  fireEvent.click(screen.getByRole("option", { name: "Account 104" }));
  await waitFor(() => expect(screen.queryByRole("option", { name: "Account 104" })).toBeNull());
});
