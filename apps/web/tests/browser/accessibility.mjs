import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { observeAnimations } from "./animation-recorder.mjs";

const tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const reports = path.resolve(import.meta.dirname, "../../../../reports/accessibility");

export function assertAccessible(results, label) {
  assert.deepEqual(
    results.violations.map((rule) => ({
      rule: rule.id,
      impact: rule.impact,
      help: rule.helpUrl,
      nodes: rule.nodes.map((node) => ({ target: node.target, reason: node.failureSummary })),
    })),
    [],
    `Accessibility violations: ${label}`,
  );
}

async function tabTo(page, target) {
  // Start at the beginning, then use the browser's actual sequential focus order.
  await page.evaluate(() => {
    const previous = document.body.getAttribute("tabindex");
    document.body.tabIndex = -1;
    document.body.focus();
    if (previous === null) document.body.removeAttribute("tabindex");
    else document.body.setAttribute("tabindex", previous);
  });
  for (let index = 0; index < 120; index++) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((node) => node === document.activeElement)) return;
  }
  assert.fail(`Control cannot be reached with Tab: ${await target.getAttribute("class")}`);
}

async function fitsViewport(page) {
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    "Reader content must reflow without page-level horizontal scrolling",
  );
  const bookmark = page.locator(".bookmark-button:visible").first();
  const bounds = await bookmark.boundingBox();
  assert.ok(bounds.width >= 24 && bounds.height >= 24, "Bookmark target remains usable");
}

async function activateGuestLink(page, link) {
  assert.match(await link.getAttribute("href"), /\/login\?/);
  // Verify native keyboard activation without leaving the deterministic reader fixture.
  await link.evaluate((node) => {
    node.addEventListener(
      "click",
      (event) => {
        event.preventDefault();
        node.dataset.keyboardActivated = "true";
      },
      { once: true },
    );
  });
  await page.keyboard.press("Enter");
  assert.equal(await link.getAttribute("data-keyboard-activated"), "true");
}

/** Shared checks run in the website and each actual built extension, with fixture APIs only. */
export async function checkAccessibility(page, surface, { signedIn = false } = {}) {
  const state = signedIn ? "signed-in" : "guest";
  const directory = path.join(reports, surface, state);
  await rm(directory, { recursive: true, force: true });
  await mkdir(directory, { recursive: true });
  const original = {
    viewport: page.viewportSize(),
    dark: await page.evaluate(() => document.documentElement.classList.contains("dark")),
    reducedMotion: await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    ),
  };
  const report = { surface, state, status: "running", scans: [], interactions: [] };
  const save = () =>
    writeFile(path.join(directory, "summary.json"), JSON.stringify(report, null, 2));
  const step = async (name, action) => {
    try {
      await action();
      report.interactions.push({ name, status: "passed" });
    } catch (error) {
      report.interactions.push({ name, status: "failed", error: error.message });
      throw error;
    } finally {
      await save();
    }
  };
  const scan = async (name) => {
    const result = await new AxeBuilder({ page }).withTags(tags).analyze();
    await writeFile(path.join(directory, `${name}.json`), JSON.stringify(result, null, 2));
    report.scans.push({
      name,
      violations: result.violations,
      incomplete: result.incomplete.length,
      passed: result.passes.length,
    });
    await save();
  };
  const grid = page.getByRole("button", { name: "Grid view", exact: true });
  const list = page.getByRole("button", { name: "List view", exact: true });
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    await grid.click();
    await page.locator(".article-card").first().waitFor();
    await step("skip link reaches main content", async () => {
      await tabTo(page, page.locator(".skip-link"));
      await page.keyboard.press("Enter");
      assert.equal(
        await page.locator("main").evaluate((node) => node === document.activeElement),
        true,
      );
    });
    for (const dark of [false, true]) {
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value),
        dark,
      );
      await scan(`${dark ? "dark" : "light"}-grid-desktop`);
    }
    await step("keyboard switches grid to list", async () => {
      await tabTo(page, list);
      await page.keyboard.press("Enter");
      await page.locator(".article-list-row").first().waitFor();
      assert.equal(await list.getAttribute("aria-pressed"), "true");
    });
    for (const [width, dark, name] of [
      [640, false, "light-list-200-percent-reflow"],
      [320, true, "dark-list-mobile"],
    ]) {
      // At 200% browser zoom, a 1280px desktop viewport exposes 640 CSS pixels.
      // This checks equivalent layout reflow; deviceScaleFactor is not a zoom test.
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value),
        dark,
      );
      await step(name, () => fitsViewport(page));
      await scan(name);
    }
    await page.setViewportSize({ width: 640, height: 1000 });
    await step("text resized to 200% retains article and bookmark controls", async () => {
      await page.evaluate(() => {
        const size = getComputedStyle(document.documentElement).fontSize;
        document.documentElement.style.fontSize = `${parseFloat(size) * 2}px`;
      });
      try {
        await fitsViewport(page);
        await page.locator(".article-list-title").first().scrollIntoViewIfNeeded();
        assert.ok(await page.locator(".article-list-title").first().isVisible());
        await scan("dark-list-200-percent-text");
      } finally {
        await page.evaluate(() => document.documentElement.style.removeProperty("font-size"));
      }
    });
    await step("keyboard bookmark and sign-in controls", async () => {
      const bookmark = page.locator(".bookmark-button:visible").first();
      await tabTo(page, bookmark);
      if (signedIn) {
        const pressed = await bookmark.getAttribute("aria-pressed");
        await page.keyboard.press("Space");
        await page.waitForFunction(
          (value) =>
            document
              .querySelector(".bookmark-button[aria-pressed]")
              ?.getAttribute("aria-pressed") !== value,
          pressed,
        );
        await page
          .getByRole("status")
          .filter({ hasText: /Saved to Read later|Removed from Read later/ })
          .first()
          .waitFor();
        await page.locator(".bookmark-button:visible:not([aria-busy='true'])").first().waitFor();
        assert.ok(
          await bookmark.evaluate((node) => node === document.activeElement),
          "Saving a bookmark retains keyboard focus",
        );
        await page.keyboard.press("Enter");
        await page.waitForFunction(
          (value) =>
            document
              .querySelector(".bookmark-button[aria-pressed]")
              ?.getAttribute("aria-pressed") === value,
          pressed,
        );
      } else {
        assert.match(await bookmark.getAttribute("aria-label"), /Sign in to save/);
        await activateGuestLink(page, bookmark);
        const login = page.getByRole("link", { name: "Sign in", exact: true }).first();
        await tabTo(page, login);
        await activateGuestLink(page, login);
      }
    });
    const title = page.locator(".article-list-title").first();
    const dialog = page.getByRole("dialog", { name: "Article preview", exact: true });
    await step("preview focus, tab containment, Escape and focus restoration", async () => {
      await tabTo(page, title);
      await page.keyboard.press("Enter");
      await dialog.locator("#article-preview-title").waitFor();
      assert.ok(await dialog.evaluate((node) => node.contains(document.activeElement)));
      const count = await dialog
        .locator("a[href], button:not(:disabled), input, select, textarea")
        .count();
      for (const key of ["Tab", "Shift+Tab"]) {
        for (let index = 0; index < count + 2; index++) {
          await page.keyboard.press(key);
          assert.ok(
            await dialog.evaluate((node) => node.contains(document.activeElement)),
            `Preview retains ${key} focus`,
          );
        }
      }
      for (const dark of [false, true]) {
        await page.evaluate(
          (value) => document.documentElement.classList.toggle("dark", value),
          dark,
        );
        await scan(`${dark ? "dark" : "light"}-preview`);
      }
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assert.ok(await title.evaluate((node) => node === document.activeElement));
    });
    await step("reduced motion disables reader and preview animations", async () => {
      const animations = await observeAnimations(page, async () => {
        await title.press("Enter");
        await dialog.locator("#article-preview-title").waitFor();
        assert.equal(
          await dialog.evaluate((node) => getComputedStyle(node, "::backdrop").animationName),
          "none",
        );
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "detached" });
      });
      assert.deepEqual(animations, []);
    });
    await page.setViewportSize({ width: 320, height: 1000 });
    await grid.click();
    await page.locator(".article-card").first().waitFor();
    await step("grid mobile reflow", () => fitsViewport(page));
    await scan("dark-grid-mobile");
    for (const result of report.scans)
      assertAccessible(result, `${surface}/${state}/${result.name}`);
    report.status = "passed";
    console.log(
      `Accessibility: ${surface}/${state}: ${report.scans.length} scans and ${report.interactions.length} interaction checks passed.`,
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.message;
    await page
      .screenshot({ path: path.join(directory, "failure.png"), animations: "disabled" })
      .catch(() => {});
    throw error;
  } finally {
    await save();
    await page.evaluate(
      (dark) => document.documentElement.classList.toggle("dark", dark),
      original.dark,
    );
    await page.setViewportSize(original.viewport);
    await page.emulateMedia({ reducedMotion: original.reducedMotion ? "reduce" : "no-preference" });
  }
}
