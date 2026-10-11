// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerPlacements } from "@/components/partner-placements";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { hidePartnerProduct } from "@/lib/partner-placement-preferences";
import { placement, sponsoredPlacement } from "./partner-placement-fixtures";

const session = vi.hoisted(() => ({
  user: null as null | { user_id: string; csrf_token: string },
  loading: false,
  sessionRevision: 0,
}));
const prompts = vi.hoisted(() => ({
  current: null as null | { ready: boolean; modalOpen: boolean },
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/reader-prompts", () => ({
  useReaderPromptCoordinator: () => prompts.current,
}));
vi.mock("@/lib/reader-runtime", () => ({ readerRequest: vi.fn() }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));
beforeEach(() => {
  localStorage.clear();
  session.user = null;
  session.loading = false;
  session.sessionRevision = 0;
  prompts.current = null;
  vi.mocked(readerRequest).mockImplementation(async () =>
    Response.json({ items: [placement, sponsoredPlacement] }),
  );
  vi.mocked(userRequest).mockResolvedValue({ items: [placement] });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  localStorage.clear();
});

it("renders relevant and labelled sponsored products with safe destinations and signed tracking", async () => {
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getByText("Sponsored")).toBeDefined();
  expect(screen.getAllByText(placement.reason)).toHaveLength(2);
  expect(screen.getByRole("link", { name: sponsoredPlacement.name }).getAttribute("rel")).toBe(
    "sponsored noopener noreferrer",
  );
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(2));
  expect(vi.mocked(readerRequest).mock.calls[0][1]).toMatchObject({
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  expect(JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string)).toEqual({
    surface: "article",
    article_id: placement.product_id,
    exclude_product_ids: [],
  });
});

it("hides a product persistently and enforces exclusions even if an upstream returns it", async () => {
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  fireEvent.click(screen.getByRole("button", { name: `Hide ${placement.name}` }));
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  mounted.unmount();
  render(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls.at(-1)![1]!.body as string);
  expect(query.exclude_product_ids).toContain(placement.product_id);
});

it("counts only viewable impressions toward the cap and avoids repetition on remount", async () => {
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  act(() => vi.mocked(observePartnerPlacement).mock.calls[0][2]!());
  mounted.unmount();
  render(<PartnerPlacements surface="article" article_id={sponsoredPlacement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
});

it("deduplicates the same product across simultaneously mounted contexts", async () => {
  render(
    <>
      <PartnerPlacements surface="article" article_id={placement.product_id} />
      <PartnerPlacements surface="topic" topic_id={sponsoredPlacement.product_id} />
    </>,
  );
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getAllByRole("link", { name: placement.name })).toHaveLength(1);
});

it("uses the user endpoint and CSRF for personalized delivery and clears products at session change", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  const mounted = render(<PartnerPlacements surface="feed" />);
  await screen.findByRole("link", { name: placement.name });
  expect(userRequest).toHaveBeenCalledWith(
    "partner-placements/query",
    expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      cache: "no-store",
    }),
  );
  expect(readerRequest).not.toHaveBeenCalled();
  session.user = null;
  session.loading = true;
  mounted.rerender(<PartnerPlacements surface="feed" />);
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
});

it("keeps an anonymous article delivery unchanged when a reader session resolves or refreshes", async () => {
  session.loading = true;
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  session.loading = false;
  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision = 1;
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  session.user = { user_id: "reader", csrf_token: "rotated-csrf" };
  session.sessionRevision = 2;
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(userRequest).not.toHaveBeenCalled();
  expect(vi.mocked(readerRequest).mock.calls[0][1]?.headers).toEqual({
    "Content-Type": "application/json",
  });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  expect(observePartnerPlacement).toHaveBeenCalledTimes(2);
});

it("honors a newly lowered account cap before rendering without changing stored impression counts", async () => {
  const stored = {
    version: 1,
    entries: [
      {
        id: placement.product_id,
        day: new Date().toISOString().slice(0, 10),
        count: 2,
        cap: 3,
        seen: Date.now() - 31 * 60_000,
        hidden: 0,
      },
    ],
  };
  localStorage.setItem("devfeed:partner-placement-preferences:v1", JSON.stringify(stored));
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [{ ...placement, frequency_cap: 1 }, sponsoredPlacement],
    }),
  );
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string);
  expect(query.exclude_product_ids).toEqual([]);
  expect(JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1")!)).toEqual(
    stored,
  );
});

it("gives an article modal its candidates instead of reserving them in a signed-in background feed", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  prompts.current = { ready: true, modalOpen: false };
  vi.mocked(userRequest).mockResolvedValue({ items: [placement, sponsoredPlacement] });
  const contexts = (
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed" />
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>
  );
  const mounted = render(contexts);
  await screen.findByRole("link", { name: placement.name });
  expect(readerRequest).not.toHaveBeenCalled();
  prompts.current = { ready: true, modalOpen: true };
  mounted.rerender(
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed" />
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("article").querySelectorAll(".partner-placement")).toHaveLength(2),
  );
  expect(screen.getByTestId("background").querySelectorAll(".partner-placement")).toHaveLength(0);
  expect(userRequest).toHaveBeenCalledTimes(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(
    JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string).exclude_product_ids,
  ).toEqual([]);
});

it("renders nothing for empty, unavailable, or unsafe results and never substitutes random products", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [] }));
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  expect(mounted.container.innerHTML).toBe("");
  mounted.unmount();
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, url: "javascript:alert(1)" }] }),
  );
  const unsafe = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(unsafe.container.innerHTML).toBe("");
  unsafe.unmount();
  vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
  const unavailable = render(
    <PartnerPlacements surface="article" article_id={placement.product_id} />,
  );
  await act(async () => {});
  expect(unavailable.container.innerHTML).toBe("");
});

it("keeps products suppressed when preferences cannot be trusted", async () => {
  hidePartnerProduct(placement.product_id, 2);
  localStorage.setItem("devfeed:partner-placement-preferences:v1", "bad");
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
});
