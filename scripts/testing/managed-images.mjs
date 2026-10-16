import assert from "node:assert/strict";
import sharp from "sharp";

const widths = [320, 480, 640, 768, 960];
let imageBodies;

export function withManagedImage(article) {
  article.image_url = "https://images.example.test/thumbnails/v2/fixture/960.webp";
  article.image_variants = widths.map((width) => ({
    url: `https://images.example.test/thumbnails/v2/fixture/${width}.webp`,
    width,
  }));
}

export async function mockManagedImages(context) {
  // Encode fixtures before interception so navigation cannot close a route during image work.
  imageBodies ??= Promise.all(
    widths.map(async (width) => [
      width,
      await sharp({
        create: { width, height: (width * 9) / 16, channels: 3, background: "#659ab5" },
      })
        .webp()
        .toBuffer(),
    ]),
  ).then((entries) => new Map(entries));
  const bodies = await imageBodies;
  await context.route("https://images.example.test/thumbnails/v2/**", (route) => {
    const width = Number(new URL(route.request().url()).pathname.match(/\/(\d+)\.webp$/)?.[1]);
    assert.ok(bodies.has(width), `Unknown managed image width: ${width}`);
    return route.fulfill({ contentType: "image/webp", body: bodies.get(width) });
  });
}

export async function checkManagedImages(page) {
  const image = page.locator(".article-card .card-image img").first();
  await image.waitFor();
  await page.waitForFunction(() => {
    const image = document.querySelector(".article-card .card-image img");
    return image?.complete && image.naturalWidth > 0;
  });
  assert.match(
    await image.getAttribute("srcset"),
    /320\.webp 320w,.*480\.webp 480w,.*640\.webp 640w,.*768\.webp 768w,.*960\.webp 960w/,
  );
  assert.match(
    await image.evaluate((element) => element.currentSrc),
    /^https:\/\/images\.example\.test\/thumbnails\/v2\//,
  );
}
