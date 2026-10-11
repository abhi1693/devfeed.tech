// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerPlacements } from "@/components/partner-placements";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
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

it("discloses every compact product as sponsored while retaining its signed asset tracking", async () => {
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getAllByText("Sponsored")).toHaveLength(2);
  expect(screen.queryByText(placement.reason)).toBeNull();
  expect(screen.queryByText("Relevant product")).toBeNull();
  expect(screen.queryByText(`From ${placement.partner_name}`)).toBeNull();
  const card = screen.getByRole("link", { name: placement.name }).closest(".partner-placement")!;
  const logo = card.querySelector("img")!;
  expect(logo.getAttribute("src")).toBe(placement.logo_url);
  expect(logo.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(logo.getAttribute("alt")).toBe("");
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

it("replaces broken or absent product logos with stable initials without hiding the product", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [placement, { ...sponsoredPlacement, logo_url: null }] }),
  );
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  const title = await screen.findByRole("link", { name: placement.name });
  const card = title.closest(".partner-placement")!;
  fireEvent.error(card.querySelector("img")!);
  expect(card.querySelector("img")).toBeNull();
  expect(card.querySelector(".partner-placement-logo")?.textContent).toBeTruthy();
  const paidCard = screen
    .getByRole("link", { name: sponsoredPlacement.name })
    .closest(".partner-placement")!;
  expect(paidCard.querySelector("img")).toBeNull();
  expect(paidCard.querySelector(".partner-placement-logo")?.textContent).toBeTruthy();
  expect(title.getAttribute("href")).toBe(placement.url);
});

it.each([undefined, false, true])(
  "opts into sponsored fallback only when explicitly requested (%s)",
  async (allow_sponsored_fallback) => {
    render(
      <PartnerPlacements surface="feed" allow_sponsored_fallback={allow_sponsored_fallback} />,
    );
    await screen.findByRole("link", { name: placement.name });
    expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject({
      surface: "feed",
      allow_sponsored_fallback: allow_sponsored_fallback === true,
    });
  },
);

it.each(["compact", "card"])(
  "labels every %s product Sponsored with its own tracking receipt and no dismissal control",
  async (layout) => {
    render(
      <PartnerPlacements surface="article" article_id={placement.product_id}>
        {layout === "card" ? (cards) => <div>{cards}</div> : undefined}
      </PartnerPlacements>,
    );
    await screen.findByRole("link", { name: sponsoredPlacement.name });
    expect(screen.getAllByText("Sponsored")).toHaveLength(2);
    expect(screen.queryByRole("button")).toBeNull();
    for (const item of [placement, sponsoredPlacement]) {
      const link = screen.getByRole("link", { name: item.name });
      expect(link.getAttribute("rel")).toBe("sponsored noopener noreferrer");
      expect(link.getAttribute("href")).toBe(item.url);
      const footer = link.querySelector(".partner-placement-destination")!;
      expect(footer.textContent?.trim()).toBe("partner.example");
      expect(footer.querySelector("svg")).toBeNull();
      const disclosure = document.getElementById(link.getAttribute("aria-describedby")!);
      expect(disclosure?.textContent).toBe("Sponsored");
      expect(link.closest(".partner-placement")?.contains(disclosure)).toBe(true);
      await waitFor(() =>
        expect(observePartnerPlacement).toHaveBeenCalledWith(
          link,
          item.receipt,
          expect.any(Function),
          expect.objectContaining({ viewed: false }),
        ),
      );
    }
  },
);

it("ignores legacy saved hides and hide events for every product", async () => {
  localStorage.setItem(
    "devfeed:partner-placement-preferences:v1",
    JSON.stringify({
      version: 1,
      entries: [placement, sponsoredPlacement].map(({ product_id }) => ({
        id: product_id,
        day: new Date().toISOString().slice(0, 10),
        count: 50,
        cap: 1,
        seen: Date.now(),
        hidden: Date.now() + 90 * 86_400_000,
      })),
    }),
  );
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  act(() => {
    window.dispatchEvent(
      new CustomEvent("devfeed:partner-product-hidden", { detail: placement.product_id }),
    );
    window.dispatchEvent(
      new StorageEvent("storage", { key: "devfeed:partner-placement-preferences:v1" }),
    );
  });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
  mounted.unmount();
  render(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls.at(-1)![1]!.body as string);
  expect(query.exclude_product_ids).toEqual([]);
});

it("keeps viewed products eligible on immediate remount in another context", async () => {
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalled());
  act(() => vi.mocked(observePartnerPlacement).mock.calls[0][2]!());
  mounted.unmount();
  render(<PartnerPlacements surface="article" article_id={sponsoredPlacement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls.at(-1)![1]!.body as string);
  expect(query.exclude_product_ids).toEqual([]);
});

it("keeps delivered products visible when recording their view history fails", async () => {
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(2));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("storage unavailable");
  });
  act(() => {
    for (const [, , viewed] of vi.mocked(observePartnerPlacement).mock.calls) viewed?.();
  });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
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

it.each([
  { surface: "article" as const, article_id: placement.product_id },
  { surface: "topic" as const, topic_id: placement.product_id },
  { surface: "feed" as const, article_ids: [placement.product_id, sponsoredPlacement.product_id] },
])("uses authenticated context-only delivery on the $surface surface", async (query) => {
  session.user = { user_id: "reader", csrf_token: "private-csrf" };
  render(<PartnerPlacements {...query} />);
  await screen.findByRole("link", { name: placement.name });
  expect(readerRequest).not.toHaveBeenCalled();
  const [path, request] = vi.mocked(userRequest).mock.calls[0];
  expect(path).toBe("partner-placements/query");
  expect(request?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "private-csrf",
  });
  expect(JSON.parse(String(request?.body))).toEqual({
    ...query,
    ...(query.surface === "feed" ? { allow_sponsored_fallback: false } : {}),
    exclude_product_ids: [],
  });
});

it("waits for session resolution and replaces receipts when the authenticated session refreshes", async () => {
  session.loading = true;
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  expect(userRequest).not.toHaveBeenCalled();
  session.loading = false;
  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision = 1;
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  session.user = { user_id: "reader", csrf_token: "rotated-csrf" };
  session.sessionRevision = 2;
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(2));
  expect(vi.mocked(userRequest).mock.calls[1][1]?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "rotated-csrf",
  });
  expect(readerRequest).not.toHaveBeenCalled();
});

it.each([false, true])(
  "retains inert grid cells while verifying the same reader (authenticated: %s)",
  async (authenticated) => {
    session.user = authenticated ? { user_id: "reader", csrf_token: "csrf" } : null;
    const content = () => (
      <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
    );
    const mounted = render(content());
    await screen.findByRole("link", { name: placement.name });
    const card = mounted.container.querySelector(".partner-placement")!;
    const requests = authenticated ? userRequest : readerRequest;
    expect(requests).toHaveBeenCalledTimes(1);
    session.loading = true;
    mounted.rerender(content());
    expect(mounted.container.querySelector(".partner-placement")).toBe(card);
    expect(card.getAttribute("data-suspended")).toBe("true");
    expect(card.getAttribute("aria-hidden")).toBe("true");
    expect(card.hasAttribute("inert")).toBe(true);
    expect(card.hasAttribute("hidden")).toBe(false);
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(requests).toHaveBeenCalledTimes(1);
    session.loading = false;
    mounted.rerender(content());
    await screen.findByRole("link", { name: placement.name });
    expect(mounted.container.querySelector(".partner-placement")).toBe(card);
    expect(requests).toHaveBeenCalledTimes(1);
  },
);

it("aborts a previous account delivery and cannot show its late response for the next reader", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(userRequest).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  session.user = { user_id: "first", csrf_token: "first-csrf" };
  const mounted = render(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  const oldSignal = vi.mocked(userRequest).mock.calls[0][1]!.signal;
  session.user = { user_id: "second", csrf_token: "second-csrf" };
  session.sessionRevision += 1;
  vi.mocked(userRequest).mockResolvedValueOnce({ items: [sponsoredPlacement] });
  mounted.rerender(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => finish({ items: [placement] }));
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  session.user = null;
  mounted.rerender(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  expect(vi.mocked(readerRequest).mock.calls[0][1]?.credentials).toBe("omit");
});

it("ignores old account caps and repeated-view history when rendering relevant products", async () => {
  const stored = {
    version: 1,
    entries: [
      {
        id: placement.product_id,
        day: new Date().toISOString().slice(0, 10),
        count: 50,
        cap: 1,
        seen: Date.now(),
        hidden: 0,
      },
    ],
  };
  localStorage.setItem("devfeed:partner-placement-preferences:v1", JSON.stringify(stored));
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string);
  expect(query.exclude_product_ids).toEqual([]);
});

it("gives an article modal its candidates instead of reserving them in a signed-in background feed", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  prompts.current = { ready: true, modalOpen: false };
  vi.mocked(userRequest).mockResolvedValue({ items: [placement, sponsoredPlacement] });
  const contexts = (
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
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
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("article").querySelectorAll(".partner-placement")).toHaveLength(2),
  );
  expect(
    screen
      .getByTestId("background")
      .querySelector(".partner-placement")
      ?.getAttribute("data-suspended"),
  ).toBe("true");
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(
    JSON.parse(vi.mocked(userRequest).mock.calls[1][1]!.body as string).exclude_product_ids,
  ).toEqual([]);
  prompts.current = { ready: true, modalOpen: false };
  mounted.rerender(
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>,
  );
  await waitFor(() =>
    expect(
      screen
        .getByTestId("background")
        .querySelector(".partner-placement")
        ?.getAttribute("data-suspended"),
    ).not.toBe("true"),
  );
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(readerRequest).not.toHaveBeenCalled();
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

it.each(["malformed", "unreadable", "unwritable"])(
  "delivers all products with %s optional history storage",
  async (failure) => {
    if (failure === "malformed")
      localStorage.setItem("devfeed:partner-placement-preferences:v1", "bad");
    else
      vi.spyOn(
        Storage.prototype,
        failure === "unreadable" ? "getItem" : "setItem",
      ).mockImplementation(() => {
        throw new Error("storage unavailable");
      });
    render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
    await screen.findByRole("link", { name: placement.name });
    expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
    expect(screen.getAllByText("Sponsored")).toHaveLength(2);
    expect(screen.queryByRole("button")).toBeNull();
    expect(
      JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).exclude_product_ids,
    ).toEqual([]);
  },
);
