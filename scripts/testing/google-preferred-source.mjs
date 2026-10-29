import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";

const eventName = "preferred_source_button_click";

/** Activate the real widget/packaged badge without confirming a Google selection. */
export async function checkGooglePreferredSource(page, screenshot, options = {}) {
  const modal = page.getByRole("dialog", { name: "Article preview" });
  const frame = modal.locator(".google-preferred-source-widget");
  const website = (await frame.count()) > 0;
  const control = website
    ? frame.contentFrame().getByRole("button", { name: /preferred sources?/i })
    : modal.getByRole("link", {
        name: "Add DevFeed as a preferred source on Google (opens in a new tab)",
        exact: true,
      });
  const href = "https://www.google.com/preferences/source?q=devfeed.tech";
  const popupPattern = website ? "https://news.google.com/swg/ui/v1/addpreferredsource**" : href;
  try {
    await control.waitFor({ state: "visible", timeout: 30000 });
  } catch (error) {
    if (website)
      console.error(
        "Preferred-source widget diagnostics",
        await frame
          .contentFrame()
          .locator("body")
          .evaluate((body) => ({
            text: body.innerText,
            scriptCount: document.scripts.length,
            host: body.querySelector("[google-add-preferred-source-btn]")?.outerHTML,
            shadow: body
              .querySelector("[google-add-preferred-source-btn]")
              ?.shadowRoot?.innerHTML.slice(-1500),
            buttons: [...body.querySelectorAll("button")].map((node) => ({
              text: node.textContent,
              hidden: node.hidden,
            })),
          })),
      );
    throw error;
  }
  assert.equal(await modal.locator(".preview-preferred-source").count(), 1);
  assert.equal(await page.locator(".sidebar [data-google-preferred-source]").count(), 0);
  assert.equal(await page.locator(".reader-mobile-secondary").count(), 0);
  if (website) {
    assert.equal(await frame.getAttribute("tabindex"), "0");
    assert.equal(await frame.getAttribute("title"), "Add DevFeed as a preferred source on Google");
    assert.equal(await page.locator('script[src*="news.google.com/swg/"]').count(), 0);
    assert.equal(
      await frame
        .contentFrame()
        .locator('script[src="https://news.google.com/swg/js/v1/publisher.js"]')
        .count(),
      1,
    );
  } else {
    assert.equal(await control.getAttribute("href"), href);
    assert.equal(await control.getAttribute("target"), "_blank");
    assert.equal(await control.getAttribute("rel"), "noopener noreferrer");
    const image = control.locator("img");
    assert.match(
      await image.getAttribute("src"),
      /^\/google-preferred-source\/(?:light|dark)\.webp$/,
    );
    await image.evaluate((node) => node.decode());
    assert.equal(await image.evaluate((node) => node.naturalWidth), 676);
    assert.equal(await image.evaluate((node) => node.naturalHeight), 213);
    assert.equal(
      await image.evaluate((node) => new URL(node.src).origin),
      await page.evaluate(() => window.location.origin),
    );
    const themes = await page.evaluate(async () => {
      const decoded = [];
      for (const theme of ["light", "dark"]) {
        const badge = new Image();
        badge.src = `/google-preferred-source/${theme}.webp`;
        await badge.decode();
        decoded.push({ width: badge.naturalWidth, height: badge.naturalHeight });
      }
      return decoded;
    });
    assert.deepEqual(themes, [
      { width: 676, height: 213 },
      { width: 676, height: 213 },
    ]);
    assert.equal(await page.locator('script[src*="news.google.com/swg/"]').count(), 0);
  }
  const metricCount = async () => {
    if (options.analytics === "extension")
      return options.events.filter((item) => item.event.name === eventName).length;
    if (options.analytics === "ga4")
      return page.evaluate(
        (name) =>
          (window.dataLayer ?? []).filter((item) => item[0] === "event" && item[1] === name).length,
        eventName,
      );
    return 0;
  };
  const checkToast = async () => {
    if (!website) return;
    const widget = frame.contentFrame();
    const theme = await page.evaluate(() =>
      document.documentElement.classList.contains("dark") ? "dark" : "light",
    );
    assert.equal(await frame.evaluate((node) => getComputedStyle(node).colorScheme), theme);
    assert.equal(
      await widget.locator("html").evaluate((node) => getComputedStyle(node).colorScheme),
      theme,
    );
    const before = await metricCount();
    const normal = await frame.boundingBox();
    const buttonBefore = await control.boundingBox();
    const toastUrl = "https://news.google.com/swg/ui/v1/toastiframe?devfeed-resize-fixture=1";
    await page.context().route(toastUrl, (route) =>
      route.fulfill({
        contentType: "text/html",
        body: "<!doctype html><title>Toast size fixture</title><style>body{margin:0;min-width:313px}</style><p>Google confirmation area</p>",
      }),
    );
    try {
      await widget.locator("body").evaluate((body, src) => {
        let toast = body.querySelector("iframe.publisher-toast");
        const created = !toast;
        if (!toast) {
          toast = document.createElement("iframe");
          toast.className = "publisher-toast";
          toast.src = src;
          body.appendChild(toast);
        }
        window.__preferredSourceToastFixture = {
          toast,
          created,
          style: toast.getAttribute("style"),
        };
        toast.style.cssText = "position:fixed;bottom:0;left:0;width:100%;height:200px;border:0";
      }, toastUrl);
      await page.waitForFunction(
        () => document.querySelector(".google-preferred-source-widget")?.height === "264",
      );
      await frame.scrollIntoViewIfNeeded();
      const expanded = await frame.boundingBox();
      assert.equal(expanded.height, 264);
      const viewportWidth = page.viewportSize().width;
      const clearance = viewportWidth <= 348 ? 6 : 36;
      assert.equal(Math.round(expanded.width), Math.min(360, viewportWidth - clearance));
      assert.ok(expanded.x >= 0 && expanded.x + expanded.width <= page.viewportSize().width);
      if (page.viewportSize().width > 900) {
        assert.ok(Math.abs(normal.x + normal.width - (expanded.x + expanded.width)) <= 1);
        const buttonAfter = await control.boundingBox();
        assert.ok(Math.abs(buttonBefore.x - buttonAfter.x) <= 1);
      }
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      const scrollArea = page.locator(".preview-scroll");
      const dimensions = await scrollArea.evaluate((node) => ({
        scroll: node.scrollWidth,
        client: node.clientWidth,
      }));
      assert.ok(dimensions.scroll <= dimensions.client + 1, JSON.stringify(dimensions));
      const toast = widget.locator("iframe.publisher-toast");
      const injected = await widget
        .locator("body")
        .evaluate(() => window.__preferredSourceToastFixture.created);
      if (injected) {
        await toast.contentFrame().getByText("Google confirmation area", { exact: true }).waitFor();
        const content = await toast
          .contentFrame()
          .locator("body")
          .evaluate((node) => ({
            scroll: node.scrollWidth,
            viewport: innerWidth,
          }));
        assert.ok(content.scroll <= content.viewport + 1, JSON.stringify(content));
      }
      const visible = await widget.locator("iframe.publisher-toast").evaluate((node) => {
        const box = node.getBoundingClientRect();
        return (
          box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth
        );
      });
      assert.equal(visible, true, "Google's 200px confirmation area fits inside its resized frame");
      assert.equal(await metricCount(), before, "resizing never measures a new selection flow");
    } finally {
      await widget.locator("body").evaluate(() => {
        const { toast, created, style } = window.__preferredSourceToastFixture;
        if (created) toast.remove();
        else if (style === null) toast.removeAttribute("style");
        else toast.setAttribute("style", style);
        delete window.__preferredSourceToastFixture;
      });
      await page.context().unroute(toastUrl);
    }
    await page.waitForFunction(
      () => document.querySelector(".google-preferred-source-widget")?.height === "52",
    );
  };
  const originalViewport = page.viewportSize();
  const sourceUrl = page.url();
  const context = page.context();
  const navigations = [];
  const intercept = async (route) => {
    navigations.push(route.request());
    await route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><title>Google source selection fixture</title><link rel="icon" href="data:,"><h1>Choose a preferred source</h1>',
    });
  };
  await context.route(popupPattern, intercept);
  try {
    const viewports = [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
      ...(website ? [{ width: 320, height: 844 }] : []),
    ];
    for (const [index, viewport] of viewports.entries()) {
      await page.setViewportSize(viewport);
      await (website ? frame : control).scrollIntoViewIfNeeded();
      await control.scrollIntoViewIfNeeded();
      await checkToast();
      await (website ? frame : control).scrollIntoViewIfNeeded();
      await control.scrollIntoViewIfNeeded();
      const box = await (website ? frame : control).boundingBox();
      assert.ok(
        box &&
          box.x >= 0 &&
          box.x + box.width <= viewport.width &&
          box.y >= 0 &&
          box.y + box.height <= viewport.height &&
          box.height >= 44,
      );
      const targetBox = await control.boundingBox();
      assert.ok(
        targetBox && targetBox.height >= (website ? 1 : 44),
        JSON.stringify({
          frame: box,
          target: targetBox,
          style: await control.evaluate((node) => ({
            tag: node.tagName,
            text: node.textContent,
            display: getComputedStyle(node).display,
            height: getComputedStyle(node).height,
            clientRects: node.getClientRects().length,
          })),
        }),
      );
      assert.ok(
        targetBox.x >= box.x && targetBox.x + targetBox.width <= box.x + box.width + 1,
        "the entire Google badge fits without horizontal clipping",
      );
      const before = await metricCount();
      const delivered =
        options.analytics === "extension"
          ? page.waitForResponse(
              (response) =>
                response.request().method() === "POST" &&
                new URL(response.url()).pathname === "/api/v1/extension/analytics" &&
                response.request().postDataJSON()?.event?.name === eventName,
            )
          : undefined;
      const opened = context.waitForEvent("page");
      if (index === 0) {
        await control.focus();
        await page.keyboard.press("Enter");
      } else await control.click();
      const target = await opened;
      try {
        await target.getByRole("heading", { name: "Choose a preferred source" }).waitFor();
        const destination = new URL(target.url());
        if (website) {
          assert.equal(destination.origin, "https://news.google.com");
          assert.equal(destination.pathname, "/swg/ui/v1/addpreferredsource");
          assert.equal(
            destination.searchParams.get("source"),
            await frame.getAttribute("src").then((src) => new URL(src, sourceUrl).href),
          );
        } else {
          assert.equal(target.url(), href);
          assert.equal(navigations.at(-1).headers().referer, undefined);
          assert.equal(await target.evaluate(() => window.opener), null);
        }
        assert.equal(navigations.length, index + 1);
        assert.equal(page.url(), sourceUrl);
        if (delivered) await delivered;
        if (options.analytics === "ga4")
          await page.waitForFunction(
            ({ name, count }) =>
              (window.dataLayer ?? []).filter((item) => item[0] === "event" && item[1] === name)
                .length >= count,
            { name: eventName, count: before + 1 },
          );
        assert.equal(await metricCount(), options.analytics ? before + 1 : 0);
        assert.equal(await control.getAttribute("aria-pressed"), null);
      } finally {
        await target.close();
      }
      await page.bringToFront();
      if (screenshot) {
        const suffix =
          index === 0 ? ".png" : viewport.width === 320 ? "-narrow.png" : "-mobile.png";
        const filename = screenshot.replace(/\.png$/, suffix);
        await mkdir(dirname(filename), { recursive: true });
        await page.screenshot({ path: filename });
      }
    }
    if (options.analytics === "extension") {
      const events = options.events.filter((item) => item.event.name === eventName);
      assert.deepEqual(
        events.map((item) => item.event.params),
        [{}, {}],
      );
    } else if (options.analytics === "ga4") {
      const events = await page.evaluate(
        (name) =>
          (window.dataLayer ?? [])
            .filter((item) => item[0] === "event" && item[1] === name)
            .map((item) => item[2]),
        eventName,
      );
      assert.deepEqual(
        events.slice(-viewports.length),
        viewports.map(() => ({ send_to: options.measurementId })),
      );
    }
  } finally {
    await context.unroute(popupPattern, intercept);
    await page.setViewportSize(originalViewport);
    await page.bringToFront();
  }
}
