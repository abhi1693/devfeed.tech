// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { HeaderSponsorshipProvider, HeaderSponsorshipSlot } from "@/components/header-sponsorship";
import { readerRequest } from "@/lib/reader-runtime";
import type { PartnerPlacement } from "@/lib/partner-placements";
import { article } from "./fixtures";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

const preferences = vi.hoisted(() => ({ view: "compact" as "cards" | "compact", loading: false }));
vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false, sessionRevision: 0 }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));
vi.mock("@/lib/reader-runtime", () => ({
  readerRequest: vi.fn(),
  rememberReaderArticles: vi.fn(),
}));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: ReactNode }) => children,
  ArticleEngagement: () => null,
  ArticleBookmarkButton: () => <button type="button">Save article</button>,
  ArticleReadLink: ({ children }: { children: ReactNode }) => children,
  useArticleEngagement: () => null,
}));

const articles = Array.from({ length: 24 }, (_, index) => ({
  ...article,
  id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
  slug: `table-article-${index}`,
  title: `Table article ${index}`,
}));
const context = {
  surface: "feed" as const,
  allow_sponsored_fallback: true,
  article_ids: articles.map((item) => item.id),
  ad_formats: ["feed", "engagement", "sponsored_content"] as PartnerPlacement["ad_format"][],
};
const observed = new Map<
  Element,
  { callback: IntersectionObserverCallback; observer: IntersectionObserver }
>();
const clickListeners = new Map<HTMLElement, EventListenerOrEventListenerObject>();

// JSDOM cannot issue trusted browser input. Invoke the real observer's registered
// callback explicitly; built reader browser tests cover actual mouse/keyboard input.
function activate(link: HTMLElement) {
  const listener = clickListeners.get(link);
  expect(listener).toBeDefined();
  const event = {
    type: "click",
    button: 0,
    isTrusted: true,
    target: link,
  } as unknown as MouseEvent;
  if (typeof listener === "function") listener.call(link, event);
  else listener!.handleEvent(event);
}

function requests(path: string) {
  return vi.mocked(readerRequest).mock.calls.filter(([url]) => url === path);
}
function queries() {
  return requests("/api/v1/partner-placements/query");
}
function events() {
  return requests("/api/v1/partner-tracking/events").map(([, init]) =>
    JSON.parse(String(init?.body)),
  );
}
function tableRows() {
  return [...document.querySelectorAll<HTMLTableRowElement>(".article-table tbody > tr")];
}
function show(element: Element) {
  const { callback, observer } = observed.get(element)!;
  callback(
    [{ target: element, isIntersecting: true, intersectionRatio: 1 } as IntersectionObserverEntry],
    observer,
  );
}

beforeEach(() => {
  preferences.view = "compact";
  preferences.loading = false;
  observed.clear();
  clickListeners.clear();
  const addEventListener = HTMLElement.prototype.addEventListener;
  vi.spyOn(HTMLElement.prototype, "addEventListener").mockImplementation(function (
    this: HTMLElement,
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ) {
    if (type === "click" && this.matches("a.partner-placement-link"))
      clickListeners.set(this, listener);
    addEventListener.call(this, type, listener, options);
  });
  vi.stubGlobal(
    "IntersectionObserver",
    class implements IntersectionObserver {
      readonly root = null;
      readonly rootMargin = "0px";
      readonly scrollMargin = "0px";
      readonly thresholds = [0, 0.5];
      constructor(private callback: IntersectionObserverCallback) {}
      observe(element: Element) {
        observed.set(element, {
          callback: this.callback,
          observer: this,
        });
      }
      disconnect() {
        for (const [element, entry] of observed)
          if (entry.observer === this) observed.delete(element);
      }
      unobserve(element: Element) {
        observed.delete(element);
      }
      takeRecords() {
        return [];
      }
    },
  );
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      path === "/api/v1/partner-placements/query"
        ? {
            items: [
              { ...placement, receipt: partnerReceipt({ assetId: placement.asset_id }) },
              {
                ...sponsoredPlacement,
                receipt: partnerReceipt({ assetId: sponsoredPlacement.asset_id }),
              },
            ],
          }
        : {},
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

it("inserts Feed placements as four-column native rows after the fourth and twelfth articles", async () => {
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  const first = await screen.findByRole("link", { name: placement.name });
  const second = screen.getByRole("link", { name: sponsoredPlacement.name });
  const rows = tableRows();
  expect(rows).toHaveLength(26);
  expect(rows[4]).toBe(first.closest("tr"));
  expect(rows[13]).toBe(second.closest("tr"));
  for (const link of [first, second]) {
    const row = link.closest("tr")!;
    expect(row.parentElement?.tagName).toBe("TBODY");
    expect(within(row).getAllByRole("cell")).toHaveLength(4);
    const description = link.getAttribute("aria-describedby")!;
    expect(document.getElementById(description)?.textContent).toBe("Sponsored");
    expect(
      [...row.querySelectorAll("[id]")].filter((node) => node.id === description),
    ).toHaveLength(1);
    expect(row.querySelector(".date-column .partner-placement-label")?.textContent).toBe(
      "Sponsored",
    );
    expect(row.querySelector(".partner-placement-mobile-label")?.getAttribute("aria-hidden")).toBe(
      "true",
    );
    expect(row.querySelector(".partner-placement-title")?.textContent).toContain(
      `${placement.tagline}`,
    );
    expect(row.querySelector(".partner-placement-separator")?.textContent).toBe("|");
    expect(row.querySelector(".partner-placement-description")?.textContent).toBe(
      placement.tagline,
    );
    expect(row.querySelector(".source-column")?.textContent).toBe(placement.partner_name);
    expect(row.querySelector("img, article, .card-image, .card-copy")).toBeNull();
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("nofollow noopener noreferrer");
    expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
  }
  expect(view.container.querySelector("tbody > :not(tr)")).toBeNull();
  const body = JSON.parse(String(queries()[0][1]?.body));
  expect(body.ad_formats).toEqual(["feed"]);
  expect(body).not.toHaveProperty("layout");
});

it("waits for saved compact preferences before choosing placement formats", async () => {
  preferences.loading = true;
  preferences.view = "cards";
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  expect(screen.getByRole("link", { name: articles[0].title })).toBeTruthy();
  await act(async () => {});
  expect(queries()).toHaveLength(0);
  preferences.loading = false;
  preferences.view = "compact";
  view.rerender(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: placement.name });
  expect(queries()).toHaveLength(1);
  expect(JSON.parse(String(queries()[0][1]?.body)).ad_formats).toEqual(["feed"]);
});

it("keeps unsupported Engagement and sponsored article creatives out of the table", async () => {
  vi.mocked(readerRequest).mockResolvedValue(
    Response.json({
      items: [
        { ...placement, ad_format: "engagement" },
        {
          ...sponsoredPlacement,
          ad_format: "sponsored_content",
          article,
          url: `/articles/${article.slug}`,
        },
      ],
    }),
  );
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await waitFor(() => expect(queries()).toHaveLength(1));
  await act(async () => {});
  expect(tableRows()).toHaveLength(24);
  expect(view.container.querySelector("tbody .partner-placement, tbody article")).toBeNull();
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
});

it("uses independent delivery for the global header sponsor while keeping only Feed rows inside tbody", async () => {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(min-width: 1280px)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  vi.mocked(readerRequest).mockImplementation(async (_, init) =>
    Response.json({
      items:
        JSON.parse(String(init?.body)).surface === "header"
          ? [{ ...placement, ad_format: "header_sponsorship" }]
          : [sponsoredPlacement],
    }),
  );
  render(
    <HeaderSponsorshipProvider>
      <header className="topbar">
        <HeaderSponsorshipSlot />
      </header>
      <ArticleGrid articles={articles} placements={context} />
    </HeaderSponsorshipProvider>,
  );
  const homepage = await screen.findByRole("link", { name: placement.name });
  expect(homepage.closest("header")?.className).toBe("topbar");
  expect(screen.getByRole("link", { name: sponsoredPlacement.name }).closest("tr")).toBe(
    tableRows()[4],
  );
  expect(document.querySelector("tbody [data-ad-format='header_sponsorship']")).toBeNull();
  expect(queries().map(([, init]) => JSON.parse(String(init?.body)))).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ surface: "feed", ad_formats: ["feed"] }),
      expect.objectContaining({ surface: "header", ad_formats: ["header_sponsorship"] }),
    ]),
  );
});

it("preserves organic row nodes and focus while delayed placements arrive and later pages append", async () => {
  let resolve!: (response: Response) => void;
  vi.mocked(readerRequest).mockReturnValue(
    new Promise<Response>((done) => {
      resolve = done;
    }),
  );
  const view = render(
    <ArticleGrid articles={articles.slice(0, 4)} placements={context} placementArticleCount={4} />,
  );
  const reading = screen.getByRole("link", { name: articles[3].title });
  const originalRow = reading.closest("tr");
  reading.focus();
  await act(async () => resolve(Response.json({ items: [placement, sponsoredPlacement] })));
  expect(screen.getByRole("link", { name: articles[3].title })).toBe(reading);
  expect(reading.closest("tr")).toBe(originalRow);
  expect(document.activeElement).toBe(reading);
  view.rerender(<ArticleGrid articles={articles} placements={context} placementArticleCount={4} />);
  const paid = screen.getByRole("link", { name: sponsoredPlacement.name });
  expect(tableRows()).toHaveLength(25);
  expect(tableRows()[4]).toBe(paid.closest("tr"));
  expect(queries()).toHaveLength(1);
});

it("uses the same signed receipt for row impressions and clicks, then preserves an inert row at expiry", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-15T12:00:00Z"));
  const receipt = partnerReceipt({ assetId: placement.asset_id });
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      path === "/api/v1/partner-placements/query" ? { items: [{ ...placement, receipt }] } : {},
    ),
  );
  await act(async () => {
    render(<ArticleGrid articles={articles} placements={context} />);
  });
  const link = screen.getByRole("link", { name: placement.name });
  const row = link.closest("tr")!;
  const reading = screen.getByRole("link", { name: articles[8].title });
  const originalRows = tableRows();
  await act(async () => {
    show(link);
    await vi.advanceTimersByTimeAsync(1000);
  });
  await act(async () => {
    fireEvent.click(link);
  });
  expect(events()).toEqual([{ kind: "impression", receipt }]);
  await act(async () => {
    activate(link);
  });
  expect(events()).toEqual([
    { kind: "impression", receipt },
    { kind: "click", receipt },
  ]);
  reading.focus();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 - 1000);
  });
  expect(row.getAttribute("data-suspended")).toBe("true");
  expect(row.hasAttribute("inert")).toBe(true);
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  expect(tableRows()).toEqual(originalRows);
  expect(screen.getByRole("link", { name: articles[8].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(observed.has(link)).toBe(false);
  await act(async () => {
    activate(link);
  });
  expect(events()).toHaveLength(2);
  expect(queries()).toHaveLength(1);
});

it("rebinds tracking when the same delivery switches between table and cards without duplicate clicks", async () => {
  const formats: PartnerPlacement["ad_format"][] = ["feed"];
  const props = { articles, placements: { ...context, ad_formats: formats } };
  const renderFeed = () => <ArticleGrid {...props} />;
  const view = render(renderFeed());
  const rowLink = await screen.findByRole("link", { name: placement.name });
  await act(async () => {
    fireEvent.click(rowLink);
  });
  expect(events().filter((event) => event.kind === "click")).toHaveLength(0);
  await act(async () => {
    activate(rowLink);
  });
  expect(events().filter((event) => event.kind === "click")).toHaveLength(1);
  preferences.view = "cards";
  view.rerender(renderFeed());
  const cardLink = screen.getByRole("link", { name: placement.name });
  expect(cardLink.closest("article")).not.toBeNull();
  expect(cardLink).not.toBe(rowLink);
  expect(observed.has(rowLink)).toBe(false);
  expect(observed.has(cardLink)).toBe(true);
  await act(async () => {
    activate(cardLink);
  });
  expect(events().filter((event) => event.kind === "click")).toHaveLength(1);
  preferences.view = "compact";
  view.rerender(renderFeed());
  expect(screen.getByRole("link", { name: placement.name }).closest("tr")).not.toBeNull();
  expect(queries()).toHaveLength(1);
});
