// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerAccountSettings } from "../src/app/partner-account-settings";
import { accountSettingsPath, type AccountSettingsSection } from "../src/lib/routes";
import { partnerAccount } from "./partner-fixtures";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account = partnerAccount({
  benefits: ["Product placements", "Native ad formats", "Monthly performance reporting"],
  approval_mode: "trusted",
});
const saved = vi.fn();
const unavailable = vi.fn();
const props = {
  account,
  csrfToken: "csrf",
  onSaved: saved,
  onUnavailable: unavailable,
  query: "?q=platform&account_offset=100",
};
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
function settings(section: AccountSettingsSection = "general") {
  return render(<PartnerAccountSettings {...props} section={section} />);
}

it("shows account details and readable benefits on General without mounting any request-backed forms", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  settings();
  expect(screen.getByRole("heading", { name: `${account.name} settings`, level: 1 })).toBeTruthy();
  expect(screen.getByText("Gold", { exact: true })).toBeTruthy();
  expect(screen.getByText("Sponsored · inherited from tier")).toBeTruthy();
  expect(screen.getByText("Trusted partner products accepted automatically")).toBeTruthy();
  const benefits = screen
    .getByRole("heading", { name: "Partnership benefits" })
    .closest("section")!;
  expect(
    within(benefits)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(account.benefits);
  expect(screen.queryByRole("form")).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Product click destination" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Automatic ad formats" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Audience targeting" })).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});
it.each(["general", "destination", "formats", "audience"] as const)(
  "provides real %s URLs and preserves account-list search and pagination",
  (section) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ product: null })));
    settings(section);
    const nav = screen.getByRole("navigation", { name: "Account settings sections" });
    const tabs = [
      { value: "general", label: "General" },
      { value: "destination", label: "Click destination" },
      { value: "formats", label: "Ad formats" },
      { value: "audience", label: "Audience" },
    ] as const;
    for (const tab of tabs) {
      const link = within(nav).getByRole("link", { name: tab.label });
      expect(link.getAttribute("href")).toBe(
        `${accountSettingsPath(account.id, tab.value)}${props.query}`,
      );
      expect(link.getAttribute("aria-current")).toBe(tab.value === section ? "page" : null);
    }
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(nav.className).toContain("overflow-x-auto");
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Settings" }).getAttribute("href")).toBe(
      `/settings${props.query}`,
    );
  },
);
it("mounts only the selected destination section and requests only its destination preview", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ product: null }));
  vi.stubGlobal("fetch", fetcher);
  settings("destination");
  await screen.findByText("A preview will appear when this account has an accepted product.");
  expect(screen.getByRole("region", { name: "Click destination" })).toBeTruthy();
  expect(screen.getByRole("form", { name: "Click destination settings" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Partner account" })).toBeNull();
  expect(screen.queryByRole("form", { name: "Ad format settings" })).toBeNull();
  expect(screen.queryByRole("form", { name: "Audience settings" })).toBeNull();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe(
    `/api/v1/partner/accounts/${account.id}/destination-preview`,
  );
});
it.each([
  { section: "formats" as const, form: "Ad format settings" },
  { section: "audience" as const, form: "Audience settings" },
])(
  "mounts only the selected $section form without requesting a destination preview",
  ({ section, form }) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    settings(section);
    expect(screen.getByRole("form", { name: form })).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Click destination settings" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Partner account" })).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("aborts a departed tab's destination preview and ignores a late access error", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = settings("destination");
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  view.rerender(<PartnerAccountSettings {...props} section="general" />);
  expect(signal.aborted).toBe(true);
  await act(async () => finish(new Response(null, { status: 401 })));
  expect(unavailable).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});
it("aborts a departed tab's pending destination save without changing the next section", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) =>
    init.method === "PUT"
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : Response.json({ product: null }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = settings("destination");
  await userEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  await userEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  const pending = fetcher.mock.calls.find(([, init]) => init.method === "PUT")!;
  view.rerender(<PartnerAccountSettings {...props} section="formats" />);
  expect((pending[1].signal as AbortSignal).aborted).toBe(true);
  await act(async () => finish(new Response(null, { status: 401 })));
  expect(screen.getByRole("form", { name: "Ad format settings" })).toBeTruthy();
  expect(unavailable).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});
it("uses qualified acceptance and a readable empty-benefits state when no extras are listed", () => {
  vi.stubGlobal("fetch", vi.fn());
  render(
    <PartnerAccountSettings
      {...props}
      account={partnerAccount({
        benefits: [],
        approval_mode: "qualified",
        effective_promotion_kind: "product",
        promotion_kind_source: "account",
      })}
      section="general"
    />,
  );
  expect(screen.getByText("Automatic capability checks")).toBeTruthy();
  expect(screen.getByText("Product recommendation · account setting")).toBeTruthy();
  expect(screen.getByText("No additional benefits listed for this account.")).toBeTruthy();
});

it("reveals a bookmarked mobile tab by scrolling only the navigation", () => {
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const left = this.getAttribute("aria-current") === "page" ? 300 : 0;
    const width = this.getAttribute("aria-label") === "Account settings sections" ? 240 : 120;
    return {
      x: left,
      y: 120,
      left,
      right: left + width,
      top: 120,
      bottom: 160,
      width,
      height: 40,
      toJSON: () => ({}),
    } as DOMRect;
  });
  const scroll = vi.spyOn(window, "scrollTo");
  settings("audience");
  const nav = screen.getByRole("navigation", { name: "Account settings sections" });
  expect(nav.scrollLeft).toBe(180);
  expect(scroll).not.toHaveBeenCalled();
});
