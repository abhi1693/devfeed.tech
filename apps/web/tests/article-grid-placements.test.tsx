// @vitest-environment jsdom
import type { ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { readerRequest } from "@/lib/reader-runtime";
import type { Article } from "@/lib/types";
import { article } from "./fixtures";
import { placement, sponsoredPlacement } from "./partner-placement-fixtures";

const preferences = vi.hoisted(() => ({
  view: "cards" as "cards" | "compact" | "stacked",
  loading: false,
}));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));
vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false, sessionRevision: 0 }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/article-card", () => ({
  ArticleCard: ({ article: item }: { article: Article }) => (
    <article className="article-card">
      <a href={`/articles/${item.slug}`}>{item.title}</a>
    </article>
  ),
}));
vi.mock("@/components/article-stacked", () => ({
  ArticleStackedFrame: ({ children }: { children: ReactNode }) => (
    <section className="article-stacked">{children}</section>
  ),
  ArticleStackedRow: ({
    article: item,
    promotion,
  }: {
    article: Article;
    promotion?: { productId: string };
  }) => (
    <article
      className={`article-stacked-row${promotion ? " partner-placement" : ""}`}
      data-product-id={promotion?.productId}
      data-ad-format={promotion ? "sponsored_content" : undefined}
    >
      <a className="card-open-link" href={`/articles/${item.slug}`}>
        {item.title}
      </a>
    </article>
  ),
}));
vi.mock("@/components/article-table", () => ({
  ArticleTableFrame: ({ children }: { children: ReactNode }) => (
    <table>
      <tbody>{children}</tbody>
    </table>
  ),
  ArticleTableRow: ({ article: item }: { article: Article }) => (
    <tr className="article-list-row">
      <td>
        <a href={`/articles/${item.slug}`}>{item.title}</a>
      </td>
    </tr>
  ),
}));
vi.mock("@/lib/reader-runtime", () => ({
  readerRequest: vi.fn(),
  rememberReaderArticles: vi.fn(),
}));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));

const articles = Array.from({ length: 24 }, (_, index) => ({
  ...article,
  id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
  slug: `article-${index}`,
  title: `Article ${index}`,
}));
const context = { surface: "feed" as const, article_ids: articles.map((item) => item.id) };

beforeEach(() => {
  localStorage.clear();
  preferences.view = "cards";
  vi.mocked(readerRequest)
    .mockReset()
    .mockImplementation(async () => Response.json({ items: [placement, sponsoredPlacement] }));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("places at most two products directly into the grid after four and twelve unchanged articles", async () => {
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  const children = Array.from(view.container.querySelector(".article-grid")!.children);
  expect(children).toHaveLength(26);
  expect(children[4].getAttribute("data-product-id")).toBe(placement.product_id);
  expect(children[13].getAttribute("data-product-id")).toBe(sponsoredPlacement.product_id);
  expect(
    children
      .filter((node) => node.matches(".article-card:not(.partner-placement)"))
      .map((node) => node.textContent),
  ).toEqual(articles.map((item) => item.title));
  expect(view.container.querySelector(".partner-placements")).toBeNull();
});

it.each([4, 11])(
  "shows one product after four organic articles in a %s-article grid",
  async (count) => {
    const items = articles.slice(0, count);
    const view = render(
      <ArticleGrid
        articles={items}
        placements={{ ...context, article_ids: items.map((item) => item.id) }}
      />,
    );
    await screen.findByRole("link", { name: sponsoredPlacement.name });
    const children = Array.from(view.container.querySelector(".article-grid")!.children);
    expect(children.filter((node) => node.matches(".partner-placement"))).toHaveLength(1);
    expect(children[Math.min(count, 4)].getAttribute("data-product-id")).toBe(
      sponsoredPlacement.product_id,
    );
    expect(children[0].matches(".article-card")).toBe(true);
  },
);

it.each([0, 1, 2, 3])(
  "keeps a sparse %s-article feed entirely organic without fetching",
  async (count) => {
    const items = articles.slice(0, count);
    const view = render(<ArticleGrid articles={items} placements={context} />);
    await act(async () => {});
    expect(readerRequest).not.toHaveBeenCalled();
    expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(0);
    expect(screen.queryAllByRole("link").map((link) => link.textContent)).toEqual(
      items.map((item) => item.title),
    );
  },
);

it("requests only Feed placements for a compact article table", async () => {
  preferences.view = "compact";
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).ad_formats).toEqual([
    "feed",
  ]);
  expect(view.container.querySelectorAll("tr.partner-placement")).toHaveLength(2);
  expect(view.container.querySelector("table")).toBeTruthy();
});

it("opens the first slot only when a three-article feed grows to four organic articles", async () => {
  const view = render(<ArticleGrid articles={articles.slice(0, 3)} placements={context} />);
  const first = screen.getByRole("link", { name: articles[0].title });
  first.focus();
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  view.rerender(<ArticleGrid articles={articles.slice(0, 4)} placements={context} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  const cells = Array.from(view.container.querySelector(".article-grid")!.children);
  expect(cells.slice(0, 4).map((cell) => cell.textContent)).toEqual(
    articles.slice(0, 4).map((item) => item.title),
  );
  expect(cells[4].getAttribute("data-product-id")).toBe(sponsoredPlacement.product_id);
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(first);
  expect(document.activeElement).toBe(first);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("keeps first-page placement density fixed when more article pages append", async () => {
  const firstPage = articles.slice(0, 4);
  const placements = { ...context, article_ids: firstPage.map((item) => item.id) };
  const view = render(
    <ArticleGrid articles={firstPage} placements={placements} placementArticleCount={4} />,
  );
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  view.rerender(
    <ArticleGrid articles={articles} placements={placements} placementArticleCount={4} />,
  );
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(1);
  expect(view.container.querySelectorAll(".article-card:not(.partner-placement)")).toHaveLength(24);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("uses the full article card structure with a managed OG hero and truthful product metadata", async () => {
  const image_url = "https://assets.partner.example/og/original.png";
  const image_variants = [
    { url: "https://assets.partner.example/og/400.webp", width: 400 },
    { url: "https://assets.partner.example/og/800.webp", width: 800 },
  ];
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, image_url, image_variants }] }),
  );
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: placement.name });
  const card = view.container.querySelector(".partner-placement")!;
  expect(card.matches(".partner-placement--card")).toBe(true);
  expect(card.querySelector(".card-copy h2")).toBeTruthy();
  expect(card.querySelector(".card-meta-row")).toBeTruthy();
  expect(card.querySelector(".card-bottom")).toBeTruthy();
  const hero = card.querySelector(".card-image img")!;
  expect(hero.getAttribute("src")).toBe(image_url);
  expect(hero.getAttribute("srcset")).toBe(
    image_variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
  );
  expect(hero.getAttribute("loading")).toBe("lazy");
  expect(hero.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(card.querySelector("time")).toBeNull();
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).ad_formats).toEqual([
    "feed",
  ]);
  expect(screen.queryByRole("button", { name: /Like article|Save article for later/ })).toBeNull();
  expect(screen.queryByText(placement.reason)).toBeNull();
});

it("rejects Engagement from an overbroad grid context and keeps Feed as a full image card", async () => {
  const image = "https://assets.partner.example/full-feed-card.webp";
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [
        { ...placement, ad_format: "engagement" },
        { ...sponsoredPlacement, image_url: image },
      ],
    }),
  );
  const view = render(
    <ArticleGrid
      articles={articles}
      placements={{
        ...context,
        ad_formats: ["feed", "engagement", "sponsored_content"],
      }}
    />,
  );
  const card = (await screen.findByRole("link", { name: sponsoredPlacement.name })).closest(
    "article",
  )!;
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).ad_formats).toEqual([
    "feed",
    "sponsored_content",
  ]);
  expect(card.className).toContain("partner-placement--card");
  expect(card.querySelector(".card-image img")?.getAttribute("src")).toBe(image);
  expect(view.container.querySelector('[data-ad-format="engagement"]')).toBeNull();
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  expect(view.container.querySelectorAll(".article-card:not(.partner-placement)")).toHaveLength(24);
});

it("does not request inventory when an overbroad grid configuration offers only Engagement", async () => {
  const view = render(
    <ArticleGrid articles={articles} placements={{ ...context, ad_formats: ["engagement"] }} />,
  );
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(view.container.querySelectorAll(".article-card")).toHaveLength(24);
});

it("falls back from a failed OG hero to the product logo and then initials inside the same hero frame", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [
        {
          ...placement,
          image_url: "https://assets.partner.example/og/missing.png",
          image_variants: null,
        },
      ],
    }),
  );
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: placement.name });
  const frame = view.container.querySelector(".partner-placement .card-image")!;
  fireEvent.error(frame.querySelector("img")!);
  expect(frame.querySelector("img")?.getAttribute("src")).toBe(placement.logo_url);
  fireEvent.error(frame.querySelector("img")!);
  expect(frame.querySelector("img")).toBeNull();
  expect(frame.textContent).toContain("TV");
  expect(view.container.querySelector(".partner-placement .card-image")).toBe(frame);
});

it.each([placement.logo_url, null])(
  "uses compact branding without a hero when no OG image exists (%s)",
  async (logo_url) => {
    vi.mocked(readerRequest).mockResolvedValueOnce(
      Response.json({ items: [{ ...placement, logo_url, image_url: null, image_variants: null }] }),
    );
    const view = render(<ArticleGrid articles={articles} placements={context} />);
    await screen.findByRole("link", { name: placement.name });
    const card = view.container.querySelector(".partner-placement--branded")!;
    expect(card.querySelector(".card-image")).toBeNull();
    const frame = card.querySelector(".partner-placement-heading")!;
    expect(frame.textContent).toContain(placement.name);
    if (logo_url) expect(frame.querySelector("img")?.getAttribute("src")).toBe(logo_url);
    else {
      expect(frame.querySelector("img")).toBeNull();
      expect(frame.textContent).toContain("TV");
    }
  },
);

it("uses an available managed image variant when the original image is absent", async () => {
  const url = "https://assets.partner.example/og/400.webp";
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [{ ...placement, image_url: null, image_variants: [{ url, width: 400 }] }],
    }),
  );
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: placement.name });
  expect(
    view.container.querySelector(".partner-placement .card-image img")?.getAttribute("src"),
  ).toBe(url);
  expect(view.container.querySelector(".partner-placement--branded")).toBeNull();
});

it("keeps article DOM and keyboard focus while optional products finish loading", async () => {
  let resolve!: (value: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  render(<ArticleGrid articles={articles} placements={context} />);
  const first = screen.getByRole("link", { name: articles[0].title });
  first.focus();
  await act(async () => resolve(Response.json({ items: [placement, sponsoredPlacement] })));
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(first);
  expect(document.activeElement).toBe(first);
});

it("keeps Feed promotions inside the Stacked stream with truthful attribution and no article actions", async () => {
  preferences.view = "stacked";
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: placement.name });
  const stream = view.container.querySelector(".article-stacked")!;
  expect(stream.children).toHaveLength(26);
  expect(stream.children[4].getAttribute("data-product-id")).toBe(placement.product_id);
  expect(stream.children[13].getAttribute("data-product-id")).toBe(sponsoredPlacement.product_id);
  const product = stream.querySelector(".partner-placement--stacked")!;
  expect(product.querySelector("h2")?.textContent).toBe(placement.name);
  expect(product.querySelector(".partner-placement-description")?.textContent).toBe(
    placement.tagline,
  );
  expect(product.querySelector('[data-promotion-kind="sponsored"]')?.textContent).toBe("Sponsored");
  expect(product.querySelector("time, button")).toBeNull();
  expect(product.querySelector("a")?.getAttribute("rel")).toContain("nofollow");
  expect(
    [...stream.querySelectorAll(".article-stacked-row:not(.partner-placement)")].map(
      (row) => row.textContent,
    ),
  ).toEqual(articles.map((item) => item.title));
});

it("retains sponsored-content eligibility in Stacked and removes the repeated organic article", async () => {
  preferences.view = "stacked";
  const promoted = { ...articles[8], topics: [] };
  // A first-page delivery can overlap an organic article appended by a later page.
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [
        {
          ...sponsoredPlacement,
          ad_format: "sponsored_content",
          url: `/articles/${promoted.slug}`,
          article: promoted,
        },
      ],
    }),
  );
  const view = render(
    <ArticleGrid
      articles={articles}
      placements={{
        ...context,
        article_ids: articles.slice(0, 4).map((item) => item.id),
        ad_formats: ["feed", "sponsored_content"],
      }}
      placementArticleCount={4}
    />,
  );
  await waitFor(() =>
    expect(view.container.querySelectorAll('[data-ad-format="sponsored_content"]')).toHaveLength(1),
  );
  const stream = view.container.querySelector(".article-stacked")!;
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).ad_formats).toEqual([
    "feed",
    "sponsored_content",
  ]);
  expect(stream.querySelectorAll('[data-ad-format="sponsored_content"]')).toHaveLength(1);
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
  expect(stream.querySelectorAll(".article-stacked-row:not(.partner-placement)")).toHaveLength(23);
});
