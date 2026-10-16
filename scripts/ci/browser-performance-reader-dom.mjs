import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { chromium } from "playwright";

const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const values = (result) =>
  Object.fromEntries(result.metrics.map(({ name, value }) => [name, value]));

/** Same-content DOM census and actual style/layout work, without network timing estimates. */
export async function auditReaderDom(origin, output) {
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const mobile of [true, false]) {
      const profile = mobile ? "mobile" : "desktop";
      const viewport = mobile ? { width: 412, height: 823 } : { width: 1350, height: 940 };
      for (const path of ["/latest", "/topics", "/sources"]) {
        const runs = [];
        for (let run = 0; run < 5; run++) {
          const context = await browser.newContext({
            viewport,
            deviceScaleFactor: mobile ? 1.75 : 1,
          });
          try {
            await context.addInitScript(() =>
              localStorage.setItem("devfeed:dev-card-promo-seen", "true"),
            );
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", (error) => errors.push(error.message));
            const cdp = await context.newCDPSession(page);
            await cdp.send("Emulation.setCPUThrottlingRate", { rate: mobile ? 4 : 1 });
            await cdp.send("Performance.enable");
            await page.goto(origin + path);
            await page.bringToFront();
            await page
              .locator(path === "/latest" ? ".article-card" : ".catalog-card")
              .first()
              .waitFor();
            await page.waitForLoadState("networkidle");
            await page.waitForFunction(
              (feed) =>
                feed
                  ? document.querySelectorAll(".article-card .open-count").length === 24
                  : document.querySelectorAll(".catalog-card a.follow-button").length === 60,
              path === "/latest",
            );
            const census = await page.evaluate(() => {
              const nodes = [...document.querySelectorAll("*")];
              const cards = [...document.querySelectorAll(".article-card, .catalog-card")];
              const subtree = (card) => ({
                elements: card.querySelectorAll("*").length + 1,
                wrappers: card.querySelectorAll("div, span").length,
                svgElements: card.querySelectorAll("svg *").length,
              });
              return {
                elements: nodes.length,
                maxDepth: Math.max(
                  ...nodes.map((node) => {
                    let depth = 0;
                    for (let p = node.parentElement; p; p = p.parentElement) depth++;
                    return depth;
                  }),
                ),
                cards: cards.length,
                firstCard: subtree(cards[0]),
                cardElements: cards.reduce((sum, card) => sum + subtree(card).elements, 0),
                tags: nodes.reduce(
                  (counts, node) => ({
                    ...counts,
                    [node.localName]: (counts[node.localName] ?? 0) + 1,
                  }),
                  {},
                ),
                content: cards.map((card) => ({
                  text: card.textContent.replace(/\s+/g, " ").trim(),
                  links: [...card.querySelectorAll("a")].map((link) => ({
                    text: link.textContent,
                    path: new URL(link.href).pathname + new URL(link.href).search,
                  })),
                })),
              };
            });
            assert.equal(census.cards, path === "/latest" ? 24 : 60);
            const initial = values(await cdp.send("Performance.getMetrics"));
            const snapshot = await cdp.send("DOMSnapshot.captureSnapshot", { computedStyles: [] });
            // A fixed resize/theme sequence exercises real responsive layout and style matching.
            const start = values(await cdp.send("Performance.getMetrics"));
            for (let cycle = 0; cycle < 6; cycle++) {
              await page.setViewportSize({
                ...viewport,
                width: viewport.width + (cycle % 2 ? 0 : 18),
              });
              await page.evaluate(() => {
                document.documentElement.classList.toggle("dark");
                return new Promise((resolve) =>
                  requestAnimationFrame(() => {
                    void document.body.offsetHeight;
                    requestAnimationFrame(resolve);
                  }),
                );
              });
            }
            const end = values(await cdp.send("Performance.getMetrics"));
            runs.push({
              census,
              layoutObjects: initial.LayoutObjects,
              layoutNodes: snapshot.documents[0].layout.nodeIndex.length,
              initialLayoutMs: initial.LayoutDuration * 1000,
              initialStyleMs: initial.RecalcStyleDuration * 1000,
              interactionLayoutMs: (end.LayoutDuration - start.LayoutDuration) * 1000,
              interactionStyleMs: (end.RecalcStyleDuration - start.RecalcStyleDuration) * 1000,
            });
            if (run === 0)
              await page.screenshot({
                path: `${output}/${profile}-${path.slice(1)}.png`,
                fullPage: true,
                animations: "disabled",
              });
            assert.deepEqual(errors, []);
          } finally {
            await context.close();
          }
        }
        const metrics = Object.keys(runs[0]).filter((key) => key !== "census");
        for (const run of runs) {
          assert.equal(
            run.census.elements,
            runs[0].census.elements,
            "All runs must reach the same settled DOM",
          );
          assert.deepEqual(
            run.census.content,
            runs[0].census.content,
            "All runs retain the same content and links",
          );
        }
        const row = {
          profile,
          path,
          census: runs[0].census,
          ...Object.fromEntries(metrics.map((key) => [key, median(runs.map((run) => run[key]))])),
          runs,
        };
        results.push(row);
        console.log(
          JSON.stringify({
            profile,
            path,
            elements: row.census.elements,
            firstCard: row.census.firstCard,
            layoutObjects: row.layoutObjects,
            interactionLayoutMs: row.interactionLayoutMs,
            interactionStyleMs: row.interactionStyleMs,
          }),
        );
      }
    }
    await writeFile(
      `${output}/measurements.json`,
      JSON.stringify({ browser: browser.version(), runs: 5, results }, null, 2),
    );
  } finally {
    await browser.close();
  }
}
