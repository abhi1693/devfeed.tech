import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { columnsFeedSelections, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";
import { scanningArticles, scanningCatalog, scanningKinds } from "./columns-editorial-fixture.mjs";

/** A stable varied board makes visual scanning problems visible in production bundles. */
export async function checkColumnsScanning(page, target, output, { baseline = false } = {}) {
  const context = page.context();
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  fixture.setColumns(
    scanningKinds.map((kind) => columnsFeedSelections.find(({ feed }) => feed.kind === kind)),
  );
  const articles = Object.fromEntries(scanningKinds.map((kind) => [kind, scanningArticles(kind)]));
  const articleKinds = new Map(
    Object.entries(articles).flatMap(([kind, items]) => items.map((item) => [item.id, kind])),
  );
  const kindFor = (url) => {
    if (url.pathname.endsWith("/user/feed")) return "recommended";
    if (url.searchParams.has("source_id")) return "source";
    if (url.searchParams.has("topic")) return "topic";
    if (url.searchParams.has("tag")) return "tag";
    return "latest";
  };
  const feedPattern = /\/api\/v1\/(?:user\/)?feed(?:\?|$)/;
  const feedHandler = (route) => {
    const kind = kindFor(new URL(route.request().url()));
    return route.fulfill({
      json: {
        items: articles[kind],
        next_cursor: null,
        ...(kind === "recommended"
          ? { has_interests: true, status: "ready", generation: "editorial-scanning", reasons: {} }
          : {}),
      },
    });
  };
  const catalogPattern = /\/api\/v1\/(sources|topics|tags)\/[^/?]+(?:\?|$)/;
  const catalogHandler = (route) => {
    const match = /\/(sources|topics|tags)\/([^/?]+)$/.exec(
      new URL(route.request().url()).pathname,
    );
    const item = scanningCatalog[match[1]].find(({ id, slug }) =>
      [id, slug].includes(decodeURIComponent(match[2])),
    );
    return item ? route.fulfill({ json: item }) : route.fallback();
  };
  const queries = [];
  const adPattern = /\/api\/v1\/(?:user\/)?partner-placements\/query$/;
  const adHandler = (route) => {
    const body = route.request().postDataJSON();
    if (!["topic", "feed"].includes(body.surface)) return route.fulfill({ json: { items: [] } });
    const kind = body.surface === "topic" ? "topic" : articleKinds.get(body.article_ids?.[0]);
    if (!kind) return route.fulfill({ json: { items: [] } });
    const index = scanningKinds.indexOf(kind) + 1;
    const productId = `b1111111-1111-4111-8111-${String(index).padStart(12, "0")}`;
    const assetId = `b2222222-2222-4222-8222-${String(index).padStart(12, "0")}`;
    const deliveryId = `b3333333-3333-4333-8333-${String(index).padStart(12, "0")}`;
    const payload = `v1.${assetId.replaceAll("-", "")}.${deliveryId.replaceAll("-", "")}.${Math.floor(Date.now() / 1000)}`;
    const receipt = `${payload}.${createHmac("sha256", "disposable-editorial-scanning-key").update(payload).digest("hex")}`;
    const long = ["recommended", "topic", "tag"].includes(kind);
    queries.push(body);
    return route.fulfill({
      json: {
        items: [
          {
            product_id: productId,
            asset_id: assetId,
            campaign_id: `b4444444-4444-4444-8444-${String(index).padStart(12, "0")}`,
            name: long
              ? "BuildScope — production telemetry for teams shipping distributed systems"
              : kind === "source"
                ? "Apviso"
                : "FastMinify",
            tagline: long
              ? "Understand releases, investigate failures, and share the production context your engineering team needs without changing its workflow."
              : "Know what users do before they leave.",
            attribution:
              kind === "tag"
                ? "Brought to you by Developer Tools Research Laboratory, the Platform Engineering Community, and the International Working Group for Distributed Systems, Open Source Infrastructure, Software Reliability, Cloud Native Architecture, Application Security, Developer Experience, and Sustainable Delivery."
                : long
                  ? "Brought to you by Developer Tools Research Laboratory and the Platform Engineering Community"
                  : "Sponsored by Apviso",
            partner_name: "Developer Tools Partner",
            platform: null,
            logo_url: null,
            image_url: null,
            image_variants: [],
            url: `https://sponsor.example.test/${kind}`,
            link_rel: ["nofollow"],
            sponsored: true,
            reason: "Relevant developer tooling",
            receipt,
            ad_format: "feed",
            article: null,
          },
        ],
      },
    });
  };
  const trackingPattern = /\/api\/v1\/partner-tracking\/events$/;
  const trackingHandler = (route) => route.fulfill({ status: 204 });
  const handlers = [
    [feedPattern, feedHandler],
    [catalogPattern, catalogHandler],
    [adPattern, adHandler],
    [trackingPattern, trackingHandler],
  ];
  for (const [pattern, handler] of handlers) await context.route(pattern, handler);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const measurements = [];
  const capture = async (name) => {
    const geometry = await workspace.evaluate((node) => {
      const box = (element) => element?.getBoundingClientRect().toJSON();
      return {
        viewport: innerWidth,
        pageWidth: document.documentElement.scrollWidth,
        images: node.querySelectorAll("img").length,
        summaryActions: node.querySelectorAll(
          '.article-stacked-summary, a[aria-label^="Read AI summary:"], a[aria-label^="Read summary:"]',
        ).length,
        media: {
          fineHover: matchMedia("(hover: hover) and (pointer: fine)").matches,
          coarse: matchMedia("(pointer: coarse)").matches,
          maxTouchPoints: navigator.maxTouchPoints,
        },
        columns: [...node.querySelectorAll("[data-column-id]")].map((column) => {
          const body = column.querySelector('[role="region"][aria-label^="Articles in "]');
          const area = body.getBoundingClientRect();
          return {
            kind: column.dataset.feedKind,
            card: box(column),
            body: box(body),
            scrollTop: body.scrollTop,
            scrollWidth: body.scrollWidth,
            clientWidth: body.clientWidth,
            visibleArticles: [
              ...body.querySelectorAll(".article-stacked-row:not(.partner-placement)"),
            ].filter((row) => {
              const r = row.getBoundingClientRect();
              return r.top < area.bottom && r.bottom > area.top;
            }).length,
            rows: [...body.querySelectorAll(".article-stacked-row")].slice(0, 6).map((row) => {
              const title = row.querySelector("h2");
              const meta = row.querySelector(".article-stacked-meta, .column-placement-meta");
              const description = row.querySelector(
                ".article-stacked-description, .partner-placement-description",
              );
              return {
                title: title?.textContent,
                row: box(row),
                headline: box(title),
                metadata: box(meta),
                description: box(description),
                fontSize: title && getComputedStyle(title).fontSize,
                lineHeight: title && getComputedStyle(title).lineHeight,
                source: row.querySelector(".article-stacked-source")?.textContent,
                domHeadlineFirst:
                  row.querySelector(".article-stacked-copy")?.firstElementChild?.tagName === "H2",
                footer: box(row.querySelector(".article-stacked-footer")),
                secondary: [
                  ...row.querySelectorAll(".article-share-trigger, .article-stacked-read"),
                ].map((action) => ({
                  className: action.className,
                  opacity: getComputedStyle(action).opacity,
                  box: box(action),
                })),
                actions: [...row.querySelectorAll(".article-stacked-actions :is(a, button)")].map(
                  (action) => ({ label: action.getAttribute("aria-label"), box: box(action) }),
                ),
                attribution: box(row.querySelector(".column-placement-attribution")),
                attributionText: row.querySelector(".column-placement-attribution")?.textContent,
                attributionStyle: row.querySelector(".column-placement-attribution") && {
                  whiteSpace: getComputedStyle(row.querySelector(".column-placement-attribution"))
                    .whiteSpace,
                  overflow: getComputedStyle(row.querySelector(".column-placement-attribution"))
                    .overflow,
                },
              };
            }),
          };
        }),
      };
    });
    assert.ok(
      geometry.pageWidth <= geometry.viewport + 1,
      `${name}: the page stays within its viewport`,
    );
    assert.equal(geometry.images, 0);
    assert.equal(geometry.summaryActions, 0, "Columns omit the duplicate summary action");
    if (name.endsWith("-top")) {
      assert.equal(
        geometry.media.fineHover,
        geometry.viewport !== 390,
        "Desktop uses a real fine pointer",
      );
      if (geometry.viewport === 390) {
        assert.equal(geometry.media.coarse, true);
        assert.equal(geometry.media.maxTouchPoints, 1);
      }
    }
    for (const column of geometry.columns) {
      assert.ok(
        column.scrollWidth <= column.clientWidth + 1,
        `${name}: ${column.kind} copy does not overflow`,
      );
      if (!baseline)
        for (const row of column.rows) {
          if (row.footer) {
            assert.equal(row.domHeadlineFirst, true, "Column headlines lead the reading order");
            assert.ok(row.headline.y < row.metadata.y, "Headlines precede metadata visually");
            assert.equal(parseFloat(row.fontSize), 16, "Headlines stay readable at every width");
            if (name.endsWith("-top"))
              for (const action of row.secondary) {
                if (geometry.media.fineHover)
                  assert.equal(Number(action.opacity), 0, "Secondary actions stay quiet at rest");
                if (geometry.media.coarse)
                  assert.equal(Number(action.opacity), 1, "Touch actions remain discoverable");
              }
            if (geometry.media.coarse)
              for (const action of row.actions)
                assert.ok(
                  action.box.width >= 44 && action.box.height >= 44,
                  `Touch action remains usable: ${action.label}`,
                );
          }
          if (row.attribution) {
            assert.equal(row.attributionStyle.whiteSpace, "nowrap");
            assert.equal(row.attributionStyle.overflow, "hidden");
            assert.ok(row.attribution.height <= 19, "Long attribution occupies a single line");
            assert.ok(
              row.headline.height <= parseFloat(row.lineHeight) * 3 + 1,
              "Product headlines stay within three lines",
            );
            assert.ok(row.description.height <= 42, "Product taglines stay within two lines");
          }
        }
    }
    measurements.push({ name, ...geometry });
    await page.screenshot({ path: `${output}-${name}.png`, animations: "disabled" });
  };
  const desktopFocus = async () => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    assert.equal(
      await page.evaluate(() => matchMedia("(hover: hover) and (pointer: fine)").matches),
      true,
      "Hover and keyboard checks use a real desktop pointer",
    );
    if (!baseline) {
      const row = columns.first().locator(".article-stacked-row:not(.partner-placement)").first();
      await row.hover();
      const hovered = await row
        .locator(".article-share-trigger, .article-stacked-read")
        .evaluateAll((actions) =>
          actions.map((action) => Number(getComputedStyle(action).opacity)),
        );
      assert.ok(
        hovered.length > 0 && hovered.every((value) => value === 1),
        "Hover reveals secondary actions",
      );
      await page.mouse.move(0, 0);
    }
    await page.keyboard.press("Tab");
    const first = columns.first().locator(".article-stacked-title").first();
    await first.focus();
    assert.equal(await first.evaluate((node) => node.matches(":focus-visible")), true);
    if (!baseline) {
      const focused = await first
        .locator("xpath=ancestor::article")
        .locator(".article-share-trigger, .article-stacked-read")
        .evaluateAll((actions) =>
          actions.map((action) => Number(getComputedStyle(action).opacity)),
        );
      assert.ok(
        focused.length > 0 && focused.every((value) => value === 1),
        "Keyboard focus reveals secondary actions",
      );
    }
    await capture("1440-dark-headline-focus");
    const ad = columns.first().locator(".partner-placement-link");
    await ad.focus();
    assert.equal(await ad.evaluate((node) => node.matches(":focus-visible")), true);
    await capture("1440-dark-sponsored-focus");
  };
  await mkdir(dirname(output), { recursive: true });
  const touch = await context.newCDPSession(page);
  try {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.goto(target);
    await workspace.waitFor();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '[aria-label="Columns workspace"] .article-stacked-row:not(.partner-placement)',
        ).length === 60 &&
        document.querySelectorAll('[aria-label="Columns workspace"] .partner-placement--column')
          .length === 5,
    );
    const nativeSponsor = workspace
      .locator("article[data-article-id]")
      .filter({ has: page.locator('[data-promotion-kind="sponsored"]') })
      .first();
    assert.equal(await nativeSponsor.locator('[data-promotion-kind="sponsored"]').count(), 1);
    assert.ok(
      await nativeSponsor.locator(".article-stacked-title").getAttribute("aria-describedby"),
      "Native sponsored articles retain an accessible disclosure",
    );
    for (const width of [1440, 1920, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
      if (width === 390)
        await touch.send("Emulation.setTouchEmulationEnabled", {
          enabled: true,
          maxTouchPoints: 1,
        });
      for (const theme of ["light", "dark"]) {
        await page.emulateMedia({ colorScheme: theme });
        await page.evaluate(() => {
          localStorage.setItem("devfeed:theme", "system");
          window.dispatchEvent(
            new StorageEvent("storage", { key: "devfeed:theme", newValue: "system" }),
          );
          document.querySelector('[aria-label="Column feeds"]').scrollLeft = 0;
          for (const column of document.querySelectorAll(
            '[role="region"][aria-label^="Articles in "]',
          ))
            column.scrollTop = 0;
          if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
          window.scrollTo(0, 0);
        });
        await page.mouse.move(0, 0);
        await page.waitForTimeout(100);
        await capture(`${width}-${theme}-top`);
        if (width !== 1920) {
          for (const kind of ["recommended", "source", "topic", "tag"]) {
            const column = workspace.locator(`[data-column-id][data-feed-kind="${kind}"]`);
            const ad = column.locator(".partner-placement--column");
            await column.evaluate((node) =>
              node.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "instant" }),
            );
            await ad.scrollIntoViewIfNeeded();
            await page.mouse.move(0, 0);
            await capture(`${width}-${theme}-${kind}-sponsored`);
          }
        }
      }
      if (width === 1920) await desktopFocus();
    }
    await writeFile(`${output}.json`, `${JSON.stringify({ measurements, queries }, null, 2)}\n`);
  } catch (error) {
    await page.screenshot({ path: `${output}-failure.png`, animations: "disabled" });
    throw error;
  } finally {
    await touch.send("Emulation.setTouchEmulationEnabled", { enabled: false }).catch(() => {});
    await touch.detach();
    for (const [pattern, handler] of handlers) await context.unroute(pattern, handler);
    await fixture.cleanup();
  }
}
