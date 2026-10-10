import assert from "node:assert/strict";

const bodies = {
  16: "UklGRjAAAABXRUJQVlA4TCQAAAAvD8ADEA8wVSMx//Mf8FATAAlDM6JRG9RzZIjo/wTgNEc/Eys=",
  32: "UklGRjYAAABXRUJQVlA4TCkAAAAvH8AHEA8wVSMx//Mf8DAMQVEGIeRvFcIZ7Ls5gYj+T4DmBR/kT8GaAgA=",
  64: "UklGRjQAAABXRUJQVlA4TCcAAAAvP8APEA8wVSMx//Mf8FDbtg0j//9z+mpkjej/BOA/ckOeelgc+AYA",
  96: "UklGRjoAAABXRUJQVlA4TC0AAAAvX8AXEA8wVSMx//Mf8DDTtk0YFML4syqEMdjVv9oV0f8JoJ++KNg0n8JJdBIA",
};
export function withManagedSourceLogo(source) {
  source.logo_url = "https://images.example.test/topic-logos/v1/source-fixture/64.webp";
  source.logo_variants = [16, 32, 64, 96].map((width) => ({
    url: `https://images.example.test/topic-logos/v1/source-fixture/${width}.webp`,
    width,
  }));
}
export async function mockSourceLogos(context) {
  await context.route("https://images.example.test/topic-logos/**", (route) => {
    const filename = new URL(route.request().url()).pathname.split("/").at(-1);
    const width = filename?.endsWith(".webp") ? filename.slice(0, -5) : "";
    assert.ok(Object.hasOwn(bodies, width), `Unknown source logo fixture: ${filename}`);
    return route.fulfill({
      contentType: "image/webp",
      headers: { "Cache-Control": "public, max-age=31536000, immutable" },
      body: Buffer.from(bodies[width], "base64"),
    });
  });
}
export async function checkSourceLogos(page) {
  const logo = page.locator(".article-card .card-source img").first();
  await logo.waitFor();
  await logo.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => {
    const image = document.querySelector(".article-card .card-source img");
    return image?.complete && image.naturalWidth > 0;
  });
  assert.equal(await logo.getAttribute("sizes"), "12px");
  const chosen = await logo.evaluate((image) => ({
    currentSrc: image.currentSrc,
    width: image.getBoundingClientRect().width,
    dpr: devicePixelRatio,
  }));
  assert.equal(chosen.width, 12);
  const expected = [16, 32, 64, 96].find((width) => width >= chosen.width * chosen.dpr);
  assert.ok(chosen.currentSrc.endsWith(`/${expected}.webp`), JSON.stringify(chosen));
}

export async function checkSourceLogoPages(page) {
  const feed = page.url();
  await page.locator(".card-open-link").first().click();
  const preview = page.locator(".preview-publisher img").first();
  await preview.waitFor();
  assert.equal(await preview.getAttribute("sizes"), "23px");
  const sourceHref = await page.locator(".preview-publisher a").first().getAttribute("href");
  const source = new URL(sourceHref, page.url()).href;
  await page.getByRole("button", { name: "Close preview", exact: true }).click();
  await page.locator("dialog.article-modal").waitFor({ state: "detached" });
  await page.goto(source);
  const header = page.locator(".feed-title img").first();
  await header.waitFor();
  assert.equal(await header.getAttribute("sizes"), "30px");
  await header.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => {
    const image = document.querySelector(".feed-title img");
    return image?.complete && image.naturalWidth > 0;
  });
  assert.match(
    await header.evaluate((image) => image.currentSrc),
    /source-fixture\/(32|64|96)\.webp$/,
  );
  await page.goto(feed);
  await page.locator(".article-card").first().waitFor();
}
