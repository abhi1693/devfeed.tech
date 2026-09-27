// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { expect, it } from "vitest";
import { CatalogIcon } from "@/components/catalog-icon";
import { devCardData } from "@/lib/dev-card";

const variants = [32, 64, 96].map((width) => ({
  url: `https://images.test/logos/${width}.webp`,
  width,
}));
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
