import { expect, it } from "vitest";
import { articleImageSources } from "@/lib/article-images";

it("delivers bounded responsive sizes and modern formats from the publisher CDN", () => {
  const image = articleImageSources(
    "https://cdn.sanity.io/images/project/dataset/asset-1536x1024.png",
  );
  const src = new URL(image.src!);
  expect(src.origin).toBe("https://cdn.sanity.io");
  expect(Object.fromEntries(src.searchParams)).toEqual({
    w: "960",
    fit: "max",
    auto: "format",
    q: "75",
  });
  expect(image.srcSet).toContain(" 640w");
  expect(image.srcSet).toContain(" 1536w");
  expect(image.srcSet).not.toContain(" 1600w");
});
it("does not upscale small originals or rewrite other hosts and existing transformations", () => {
  const small = articleImageSources(
    "https://cdn.sanity.io/images/project/dataset/asset-200x100.jpg",
  );
  expect(new URL(small.src!).searchParams.get("w")).toBe("200");
  for (const src of [
    undefined,
    "https://publisher.test/image.png",
    "https://cdn.sanity.io/images/project/dataset/asset-1536x1024.png?w=100&rect=0,0,20,20",
    "https://cdn.sanity.io.evil.test/images/p/d/asset-1536x1024.png",
  ]) {
    expect(articleImageSources(src)).toEqual({ src });
  }
});
