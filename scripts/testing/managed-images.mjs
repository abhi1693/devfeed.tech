import assert from "node:assert/strict";

export function withManagedImage(article) {
  article.image_url = "https://images.example.test/thumbnails/v2/fixture/960.webp";
  article.image_variants = [320, 640, 960].map((width) => ({
    url: `https://images.example.test/thumbnails/v2/fixture/${width}.webp`,
    width,
  }));
}

export async function mockManagedImages(context) {
  await context.route("https://images.example.test/**", (route) =>
    route.fulfill({
      contentType: "image/webp",
      body: Buffer.from(
        "UklGRpYDAABXRUJQVlA4IIoDAADQVACdASrAA5ABPp1OpE4lpCOiIAgAsBOJaW7hd1srtPEAT2Ae+2TkPfbJyHvtk5D32ych77ZOQ99snIe/AF9HBhM7DT1LCAA5bkkFAvo4MJnYaepYQAHLckgoEA99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJyHvtk5D32ych77ZOQ99snIe+2TkPfbJx/AAA/v+0b//yOv1pwc/9VbD5D0rwZPX3wMnr74GT198DJ6++Bk9ffAyevvgZPX3wMnr74GT198DJ6++Bk9ffAyevvgZPX3wMnr74GKiC+XJB8uSD5ckHy5IPlyQfLkg+XJB8uSD5ckHy5IPlyQfLkg+XJB8uSD5ckHy5IPlyQfLkg+XJB8uSD5ckHy5IPlyQfLkg+XJB8uSD5ckHy5IPlyQfN21IAAAK+EAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "base64",
      ),
    }),
  );
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
    /320\.webp 320w,.*640\.webp 640w,.*960\.webp 960w/,
  );
  assert.match(
    await image.evaluate((element) => element.currentSrc),
    /^https:\/\/images\.example\.test\/thumbnails\/v2\//,
  );
}
