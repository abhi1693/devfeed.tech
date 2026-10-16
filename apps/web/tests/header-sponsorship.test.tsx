// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HeaderSponsorshipProvider, HeaderSponsorshipSlot } from "@/components/header-sponsorship";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import type { PartnerPlacement } from "@/lib/partner-placements";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

const session = vi.hoisted(() => ({
  user: null as null | { user_id: string; csrf_token: string },
  loading: false,
}));
const prompts = vi.hoisted(() => ({ current: { ready: true, modalOpen: false } }));
const stopTracking = vi.hoisted(() => vi.fn());
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/reader-prompts", () => ({
  useReaderPromptCoordinator: () => prompts.current,
}));
vi.mock("@/lib/reader-runtime", () => ({ readerRequest: vi.fn() }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => stopTracking) }));

let media: EventTarget & { matches: boolean };
let sponsor: PartnerPlacement;
beforeEach(() => {
  vi.clearAllMocks();
  session.user = null;
  session.loading = false;
  prompts.current = { ready: true, modalOpen: false };
  sponsor = {
    ...placement,
    ad_format: "header_sponsorship",
    sponsored: true,
    campaign_id: sponsoredPlacement.campaign_id,
    receipt: partnerReceipt({ assetId: placement.asset_id }),
  };
  media = Object.assign(new EventTarget(), { matches: true });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => media),
  );
  vi.mocked(readerRequest).mockImplementation(async () => Response.json({ items: [sponsor] }));
  vi.mocked(userRequest).mockImplementation(async () => ({ items: [sponsor] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function Reader({
  page = "Home",
  slotKey = "layout",
  showSlot = true,
  articleOpen = false,
}: {
  page?: string;
  slotKey?: string;
  showSlot?: boolean;
  articleOpen?: boolean;
}) {
  return (
    <HeaderSponsorshipProvider>
      {showSlot && (
        <header key={slotKey} aria-label="Reader navigation">
          <HeaderSponsorshipSlot />
        </header>
      )}
      <main>
        <h1>{page}</h1>
      </main>
      {articleOpen && (
        <dialog open aria-label="Article preview">
          Current article
        </dialog>
      )}
    </HeaderSponsorshipProvider>
  );
}

function resize(wide: boolean) {
  act(() => {
    media.matches = wide;
    media.dispatchEvent(new Event("change"));
  });
}

it("waits for a registered wide client header before requesting its independent placement", async () => {
  expect(renderToString(<Reader />)).not.toContain(sponsor.name);
  expect(window.matchMedia).not.toHaveBeenCalled();
  expect(readerRequest).not.toHaveBeenCalled();
  media.matches = false;
  const view = render(<Reader />);
  expect(window.matchMedia).toHaveBeenCalledWith("(min-width: 1280px)");
  expect(readerRequest).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  resize(true);
  const link = await screen.findByRole("link", { name: sponsor.name });
  expect(screen.getByRole("banner", { name: "Reader navigation" }).contains(link)).toBe(true);
  expect(screen.getByRole("main").contains(link)).toBe(false);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(vi.mocked(readerRequest).mock.calls[0][0]).toBe("/api/v1/partner-placements/query");
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    surface: "header",
    ad_formats: ["header_sponsorship"],
    exclude_product_ids: [],
  });
  view.unmount();
  expect(link.isConnected).toBe(false);
});

it("does not request an unregistered header and activates when its slot becomes available", async () => {
  const view = render(<Reader showSlot={false} />);
  expect(readerRequest).not.toHaveBeenCalled();
  view.rerender(<Reader page="Search" showSlot />);
  expect(await screen.findByRole("link", { name: sponsor.name })).toBeTruthy();
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("keeps the same creative, delivery and tracking state across pages and replaced header slots", async () => {
  const view = render(<Reader />);
  const link = await screen.findByRole("link", { name: sponsor.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(1));
  const host = link.closest(".topbar-sponsorship-content");
  const tracking = vi.mocked(observePartnerPlacement).mock.calls[0][2];
  for (const page of ["Latest", "Search", "Node.js topic", "Article preview", "Bookmarks"]) {
    view.rerender(<Reader page={page} />);
    expect(screen.getByRole("heading", { name: page })).toBeTruthy();
    expect(screen.getByRole("link", { name: sponsor.name })).toBe(link);
  }
  view.rerender(<Reader page="Source feed" slotKey="source-layout" />);
  expect(screen.getByRole("link", { name: sponsor.name })).toBe(link);
  expect(link.closest(".topbar-sponsorship-content")).toBe(host);
  expect(screen.getByRole("banner", { name: "Reader navigation" }).contains(link)).toBe(true);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(observePartnerPlacement).toHaveBeenCalledTimes(1);
  expect(vi.mocked(observePartnerPlacement).mock.calls[0][2]).toBe(tracking);
  expect(stopTracking).not.toHaveBeenCalled();
});

it("stops tracking while narrow and restores the cached delivery without another request", async () => {
  const removed = vi.spyOn(media, "removeEventListener");
  const added = vi.spyOn(media, "addEventListener");
  const view = render(<Reader />);
  await screen.findByRole("link", { name: sponsor.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(1));
  const tracking = vi.mocked(observePartnerPlacement).mock.calls[0][2];
  resize(false);
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  expect(stopTracking).toHaveBeenCalledTimes(1);
  resize(true);
  await screen.findByRole("link", { name: sponsor.name });
  expect(readerRequest).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(2));
  expect(vi.mocked(observePartnerPlacement).mock.calls[1][2]).toBe(tracking);
  view.unmount();
  expect(stopTracking).toHaveBeenCalledTimes(2);
  expect(removed).toHaveBeenCalledWith("change", added.mock.calls[0][1]);
});

it("expires a continuously mounted header and stops its tracking without a page rerender", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(Math.floor(Date.now() / 1000) * 1000);
  sponsor.receipt = partnerReceipt({ assetId: sponsor.asset_id });
  await act(async () => {
    render(<Reader />);
  });
  expect(screen.getByRole("link", { name: sponsor.name })).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 - 1);
  });
  expect(screen.getByRole("link", { name: sponsor.name })).toBeTruthy();
  expect(stopTracking).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  expect(stopTracking).toHaveBeenCalledTimes(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("rechecks the header after authentication without exposing the previous owner's creative", async () => {
  const view = render(<Reader />);
  await screen.findByRole("link", { name: sponsor.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(1));
  let resolveAuthenticated!: (value: { items: PartnerPlacement[] }) => void;
  vi.mocked(userRequest).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveAuthenticated = resolve;
      }),
  );
  session.user = { user_id: "reader-1", csrf_token: "reader-csrf" };
  view.rerender(<Reader page="Search" />);
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  expect(stopTracking).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(1));
  expect(userRequest).toHaveBeenCalledWith(
    "partner-placements/query",
    expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "reader-csrf" },
    }),
  );
  const authenticated = {
    ...sponsor,
    name: "Authenticated sponsor",
    receipt: partnerReceipt({ assetId: sponsor.asset_id }),
  };
  await act(async () => resolveAuthenticated({ items: [authenticated] }));
  expect(screen.getByRole("link", { name: authenticated.name })).toBeTruthy();
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(observePartnerPlacement).toHaveBeenCalledTimes(2);
});

it("pauses tracking behind an article dialog and resumes the same header delivery on close", async () => {
  const view = render(<Reader />);
  const link = await screen.findByRole("link", { name: sponsor.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(1));
  const tracking = vi.mocked(observePartnerPlacement).mock.calls[0][2];
  prompts.current.modalOpen = true;
  view.rerender(<Reader page="Article preview" articleOpen />);
  expect(screen.getByRole("dialog", { name: "Article preview" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  expect(link.isConnected).toBe(true);
  expect(link.closest("article")?.hasAttribute("inert")).toBe(true);
  expect(stopTracking).toHaveBeenCalledTimes(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);

  prompts.current.modalOpen = false;
  view.rerender(<Reader page="Home" />);
  expect(screen.getByRole("link", { name: sponsor.name })).toBe(link);
  expect(link.closest("article")?.hasAttribute("inert")).toBe(false);
  expect(observePartnerPlacement).toHaveBeenCalledTimes(2);
  expect(vi.mocked(observePartnerPlacement).mock.calls[1]).toEqual([
    link,
    sponsor.receipt,
    tracking,
  ]);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(stopTracking).toHaveBeenCalledTimes(1);
});

it("defers an initial header request while an article dialog is already open", async () => {
  prompts.current.modalOpen = true;
  const view = render(<Reader page="Article preview" articleOpen />);
  expect(screen.getByRole("dialog", { name: "Article preview" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: sponsor.name })).toBeNull();
  expect(readerRequest).not.toHaveBeenCalled();
  expect(observePartnerPlacement).not.toHaveBeenCalled();

  prompts.current.modalOpen = false;
  view.rerender(<Reader />);
  expect(await screen.findByRole("link", { name: sponsor.name })).toBeTruthy();
  expect(readerRequest).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(1));
});
