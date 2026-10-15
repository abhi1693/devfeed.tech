// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CollectionFeedPlacements } from "@/components/collection-feed-placements";
import { readerRequest } from "@/lib/reader-runtime";
import type { PartnerPlacementContext } from "@/components/partner-placements";
import { placement, sponsoredPlacement } from "./partner-placement-fixtures";

vi.mock("@/components/user-account", () => ({ useUser: () => ({ user: null, loading: false }) }));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/lib/reader-runtime", async (original) => ({
  ...(await original<typeof import("@/lib/reader-runtime")>()),
  readerRequest: vi.fn(),
}));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));

const context: PartnerPlacementContext = {
  surface: "topic",
  topic_id: placement.product_id,
  ad_formats: ["engagement"],
};
const engagement = { ...sponsoredPlacement, ad_format: "engagement" };
function articles(count = 4) {
  return (
    <div className="article-grid">
      {Array.from({ length: count }, (_, index) => (
        <article className="article-card" key={index}>
          <a href={`/articles/${index}`}>Article {index}</a>
        </article>
      ))}
    </div>
  );
}
beforeEach(() => vi.mocked(readerRequest).mockReset());
afterEach(cleanup);

it("adds one compact sidebar without replacing organic nodes or keyboard focus", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  const view = render(
    <CollectionFeedPlacements context={context}>{articles()}</CollectionFeedPlacements>,
  );
  const grid = view.container.querySelector(".article-grid");
  const rows = [...view.container.querySelectorAll(".article-card")];
  const reading = screen.getByRole("link", { name: "Article 2" });
  reading.focus();
  expect(view.container.querySelector(".collection-feed-sidebar")).toBeNull();
  await act(async () =>
    finish(Response.json({ items: [{ ...placement, ad_format: "engagement" }, engagement] })),
  );
  const link = screen.getByRole("link", { name: engagement.name });
  const card = link.closest("article")!;
  expect(card.closest("aside")?.className).toBe("collection-feed-sidebar");
  expect(card.className).toContain("partner-placement--compact");
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(1);
  expect(view.container.querySelector(".collection-feed-articles .partner-placement")).toBeNull();
  expect(view.container.querySelector(".article-grid")).toBe(grid);
  expect([...view.container.querySelectorAll(".article-card")]).toEqual(rows);
  expect(document.activeElement).toBe(reading);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject(
    context,
  );
});

it("keeps the full organic layout without inventing an empty rail when no product is eligible", async () => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [] }));
  const view = render(
    <CollectionFeedPlacements context={context}>{articles()}</CollectionFeedPlacements>,
  );
  await act(async () => {});
  expect(view.container.querySelector("aside")).toBeNull();
  expect(view.container.querySelector(".collection-feed-layout--sidebar")).toBeNull();
  expect(view.container.querySelectorAll(".article-card")).toHaveLength(4);
});

it("retains source context and the same product when later article pages append", async () => {
  const sourceContext: PartnerPlacementContext = {
    surface: "feed",
    article_ids: [placement.product_id, sponsoredPlacement.product_id],
    ad_formats: ["engagement"],
    allow_sponsored_fallback: false,
  };
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [engagement] }));
  const view = render(
    <CollectionFeedPlacements context={sourceContext}>{articles()}</CollectionFeedPlacements>,
  );
  const link = await screen.findByRole("link", { name: engagement.name });
  view.rerender(
    <CollectionFeedPlacements context={{ ...sourceContext }}>
      {articles(24)}
    </CollectionFeedPlacements>,
  );
  expect(screen.getByRole("link", { name: engagement.name })).toBe(link);
  expect(view.container.querySelectorAll(".article-card")).toHaveLength(24);
  expect(readerRequest).toHaveBeenCalledOnce();
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject(
    sourceContext,
  );
});
