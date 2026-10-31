import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { InfiniteScroll } from "@/components/infinite-scroll";

it("renders a crawlable next-page anchor in the initial server HTML", () => {
  const html = renderToStaticMarkup(
    <InfiniteScroll
      hasMore
      loading={false}
      error={false}
      onLoadMore={vi.fn()}
      label="articles"
      nextHref="/latest?cursor=page-2"
      autoLoad={false}
    >
      <article>First page</article>
    </InfiniteScroll>,
  );

  expect(html).toContain('href="/latest?cursor=page-2"');
  expect(html).toContain('rel="next"');
  expect(html).toContain('aria-label="Next page of articles"');
  expect(html).toContain(">Next page</a>");
});

it("omits a next-page anchor at the end of a list", () => {
  const html = renderToStaticMarkup(
    <InfiniteScroll
      hasMore={false}
      loading={false}
      error={false}
      onLoadMore={vi.fn()}
      label="topics"
      nextHref={undefined}
      endMessage="You’ve seen all topics."
      autoLoad={false}
    >
      <article>Last page</article>
    </InfiniteScroll>,
  );

  expect(html).toContain("You’ve seen all topics.");
  expect(html).not.toContain("Next page");
});
