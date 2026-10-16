// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ArticleGrid } from "@/components/article-grid";
import { ArticleImage } from "@/components/article-image";
import { CARD_IMAGE_SIZES } from "@/lib/article-images";
import { article } from "./fixtures";

afterEach(cleanup);

const articles = Array.from({ length: 5 }, (_, index) => ({
  ...article,
  id: `article-${index}`,
  slug: `article-${index}`,
  image_url: `https://images.example.test/${index}.webp`,
  image_variants: [{ url: `https://images.example.test/${index}-640.webp`, width: 640 }],
}));

it("reserves high fetch priority for the first cover and keeps the other covers lazy", () => {
  const { container } = render(<ArticleGrid articles={articles} />);
  const images = [...container.querySelectorAll(".card-image img")];
  expect(images).toHaveLength(5);
  expect(images[0].getAttribute("fetchpriority")).toBe("high");
  expect(images[0].getAttribute("loading")).toBe("eager");
  expect(images[0].getAttribute("sizes")).toBe(CARD_IMAGE_SIZES);
  expect(
    images.slice(1).every((image) => image.getAttribute("sizes") === `auto, ${CARD_IMAGE_SIZES}`),
  ).toBe(true);
  expect(images.slice(1).every((image) => image.getAttribute("loading") === "lazy")).toBe(true);
  expect(images.slice(1).every((image) => image.getAttribute("fetchpriority") === "auto")).toBe(
    true,
  );
});

it("prioritizes the first real cover when earlier cards have no usable image", () => {
  const { container } = render(
    <ArticleGrid
      articles={[
        { ...articles[0], image_url: null },
        { ...articles[1], image_url: "javascript:alert(1)" },
        ...articles.slice(2),
      ]}
    />,
  );
  const images = [...container.querySelectorAll(".card-image img")];
  expect(images).toHaveLength(3);
  expect(images[0].getAttribute("loading")).toBe("eager");
  expect(images[0].getAttribute("fetchpriority")).toBe("high");
  expect(images.slice(1).every((image) => image.getAttribute("loading") === "lazy")).toBe(true);
});

it("preserves explicit preview sizing and leaves publisher fallbacks without a srcset alone", () => {
  const { container, rerender } = render(
    <ArticleImage
      label="Preview"
      src={articles[0].image_url!}
      variants={articles[0].image_variants}
      sizes="420px"
      priority
    />,
  );
  expect(container.querySelector("img")?.getAttribute("sizes")).toBe("420px");
  rerender(<ArticleImage label="Publisher" src="https://publisher.test/cover.jpg" />);
  expect(container.querySelector("img")?.getAttribute("srcset")).toBeNull();
  expect(container.querySelector("img")?.getAttribute("sizes")).toBeNull();
});

it("keeps all covers lazy when the grid is secondary content", () => {
  const { container } = render(<ArticleGrid articles={articles} priority={false} />);
  expect(container.querySelectorAll('img[fetchpriority="high"]')).toHaveLength(0);
  expect(container.querySelectorAll('.card-image img[loading="lazy"]')).toHaveLength(5);
});
