// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ArticleGrid } from "@/components/article-grid";
import { article } from "./fixtures";

afterEach(cleanup);

const articles = Array.from({ length: 5 }, (_, index) => ({
  ...article,
  id: `article-${index}`,
  slug: `article-${index}`,
  image_url: `https://images.example.test/${index}.webp`,
}));

it("reserves high fetch priority for the first cover and keeps the other covers lazy", () => {
  const { container } = render(<ArticleGrid articles={articles} />);
  const images = [...container.querySelectorAll(".card-image img")];
  expect(images).toHaveLength(5);
  expect(images[0].getAttribute("fetchpriority")).toBe("high");
  expect(images[0].getAttribute("loading")).toBe("eager");
  expect(images.slice(1).every((image) => image.getAttribute("loading") === "lazy")).toBe(true);
  expect(images.slice(1).every((image) => image.getAttribute("fetchpriority") === "auto")).toBe(
    true,
  );
});

it("keeps all covers lazy when the grid is secondary content", () => {
  const { container } = render(<ArticleGrid articles={articles} priority={false} />);
  expect(container.querySelectorAll('img[fetchpriority="high"]')).toHaveLength(0);
  expect(container.querySelectorAll('.card-image img[loading="lazy"]')).toHaveLength(5);
});
