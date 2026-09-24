// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DevCardPromo } from "@/components/dev-card-promo";
import { readDevCardDraft, saveDevCardDraft } from "@/lib/dev-card-draft";
const state = vi.hoisted(() => ({
  user: null as { user_id: string } | null,
  loading: false,
  unavailable: false,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/dev-card-artwork", () => ({
  DevCardArtwork: ({ data }: { data: { name: string } }) => <div>{data.name}</div>,
}));
beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  sessionStorage.clear();
  state.user = null;
  state.loading = false;
  state.unavailable = false;
});
afterEach(cleanup);
it("waits for authentication to resolve", () => {
  state.loading = true;
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("hides from signed-in users without a draft", () => {
  state.user = { user_id: "one" };
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("lets returning extension users finish their saved preview", async () => {
  state.user = { user_id: "one" };
  saveDevCardDraft({ name: "Maya", stack: [], ready: true, created: Date.now() });
  render(<DevCardPromo />);
  expect(
    (
      await screen.findByRole("link", { name: /Finish your dev card/ }, { timeout: 3000 })
    ).getAttribute("href"),
  ).toBe("/settings/profile");
});
it("dismisses the promotion for the session", async () => {
  const view = render(<DevCardPromo />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Dismiss dev card preview" }, { timeout: 3000 }),
  );
  view.unmount();
  render(<DevCardPromo />);
  expect(screen.queryByRole("region")).toBeNull();
});
it("ignores expired or malformed drafts", () => {
  saveDevCardDraft({ name: "Maya", stack: [], ready: true, created: Date.now() - 86400001 });
  expect(readDevCardDraft()).toBeNull();
  sessionStorage.setItem(
    "devfeed:dev-card-draft",
    JSON.stringify({ name: "Maya", stack: [null], ready: true, created: Date.now() }),
  );
  expect(readDevCardDraft()).toBeNull();
});

it("allows previewing during an auth outage without a broken signup link", async () => {
  state.unavailable = true;
  render(<DevCardPromo />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Create your dev card" }, { timeout: 3000 }),
  );
  fireEvent.change(screen.getByLabelText("Your display name"), { target: { value: "Maya" } });
  expect(screen.getByText("Maya")).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "Save my dev card" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(screen.queryByRole("link", { name: "Save my dev card" })).toBeNull();
});
