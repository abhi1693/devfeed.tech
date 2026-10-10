// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CatalogIcon } from "@/components/catalog-icon";
import { ArticlePreviewContent } from "@/components/article-preview-content";
import { devCardData } from "@/lib/dev-card";
import { article, source } from "./fixtures";

vi.mock("@/components/user-account", () => ({ useUser: () => ({ user: null, loading: true }) }));
afterEach(cleanup);

const variants = [32, 64, 96].map((width) => ({
  url: `https://images.test/logos/${width}.webp`,
  width,
}));
it("offers managed publisher variants at the preview's display size", () => {
  const { container } = render(
    <ArticlePreviewContent
      article={{
        ...article,
        sources: [{ ...source, logo_url: variants[1].url, logo_variants: variants }],
      }}
    />,
  );
  const image = container.querySelector(".preview-publisher img")!;
  expect(image.getAttribute("sizes")).toBe("23px");
  expect(image.getAttribute("srcset")).toBe(
    variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
  );
});
it("keeps a publisher fallback when preview source metadata is absent", () => {
  const { container } = render(<ArticlePreviewContent article={{ ...article, sources: [] }} />);
  const publisher = container.querySelector(".preview-publisher")!;
  expect(publisher.querySelector("img")).toBeNull();
  expect(publisher.querySelector("svg")).not.toBeNull();
  expect(publisher.textContent).toContain("example.com");
});
it("offers appropriately sized managed logos to the browser", () => {
  const { container } = render(
    <CatalogIcon url={variants[1].url} variants={variants} displaySize={22} />,
  );
  const image = container.querySelector("img")!;
  expect(image.getAttribute("sizes")).toBe("22px");
  expect(image.getAttribute("srcset")).toBe(
    variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
  );
});
it("selects the export-sized managed logo for cards", () => {
  const data = devCardData(
    {
      display_name: "Reader",
      avatar_url: null,
      stack: [
        {
          topic_id: "a",
          name: "Rancher",
          slug: "rancher",
          kind: "tool",
          status: "active",
          section: "primary",
          since_year: null,
          logo_url: variants[1].url,
          logo_variants: variants,
        },
      ],
    },
    { name: "Reader" },
  );
  expect(data.technologies[0].logoUrl).toBe(variants[2].url);
});
it("uses the fallback when a managed publisher logo fails", async () => {
  const { fireEvent } = await import("@testing-library/react");
  const { container } = render(
    <CatalogIcon url={variants[1].url} variants={variants} source displaySize={12} />,
  );
  const image = container.querySelector("img")!;
  expect(image.getAttribute("sizes")).toBe("12px");
  fireEvent.error(image);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("svg")).not.toBeNull();
});
it("excludes unsafe URLs and invalid widths from logo candidates", () => {
  const { container } = render(
    <CatalogIcon
      url={variants[1].url}
      variants={[
        ...variants,
        { url: "javascript:alert(1)", width: 16 },
        { url: variants[0].url, width: -1 },
      ]}
      source
    />,
  );
  expect(container.querySelector("img")?.getAttribute("srcset")).toBe(
    variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
  );
});
