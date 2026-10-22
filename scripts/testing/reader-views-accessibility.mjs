import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "../../apps/web/tests/browser/accessibility.mjs";
import { observeAnimations } from "../../apps/web/tests/browser/animation-recorder.mjs";
import { chooseInlineArticleView, readerRouteUrl } from "./article-view.mjs";
import { columnsFeedSelections, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";
import { scanningArticles } from "./columns-editorial-fixture.mjs";
import { openColumnMenu } from "./columns-workspace.mjs";
import { writeReaderViewReport } from "./reader-views-evidence.mjs";

const tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const selectors = {
  cards: ".article-card:not(.partner-placement)",
  compact: ".article-list-row:not(.partner-placement)",
  stacked: ".article-stacked-row[data-article-id]",
  columns: '[aria-label="Columns workspace"] .article-stacked-row[data-article-id]',
};

async function tabTo(page, target) {
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
  assert.fail("The feed layout control must be reachable in sequential keyboard focus order");
}

async function focused(page, target) {
  await page.waitForFunction(
    (node) => node === document.activeElement,
    await target.elementHandle(),
  );
  assert.equal(await target.evaluate((node) => node.matches(":focus-visible")), true);
}

/** Exercise the production reader in all runtimes, with isolated fixture APIs only. */
export async function checkSavedReaderAccessibility(
  page,
  target,
  output,
  { signedIn = false } = {},
) {
  const fixture = await installColumnsFeedFixture(page, { signedIn });
  const articles = scanningArticles("recommended").slice(0, 6);
  fixture.setSettings({ view: "cards" });
  fixture.setColumns(columnsFeedSelections.slice(0, 3));
  fixture.setOrdinaryArticles(articles);
  fixture.setVisualArticles(articles);
  const route = readerRouteUrl(target, signedIn ? "/" : "/latest");
  const originalViewport = page.viewportSize();
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const scans = [];
  const axeResults = [];
  const interactions = [];
  const motion = { webAnimations: [], css: [] };
  const proof = { signedIn, scans, interactions, motion };
  let touch;
  await mkdir(output, { recursive: true });
  await rm(join(output, "failure.png"), { force: true });
  await writeReaderViewReport(output, { status: "running", ...proof });

  const scan = async (name, include = "main") => {
    const result = await new AxeBuilder({ page }).include(include).withTags(tags).analyze();
    await writeFile(join(output, `${name}-axe.json`), `${JSON.stringify(result, null, 2)}\n`);
    scans.push({
      name,
      violations: result.violations.length,
      incomplete: result.incomplete.length,
      passed: result.passes.length,
    });
    axeResults.push({ name, result });
    await page.screenshot({ path: join(output, `${name}.png`), animations: "disabled" });
  };
  const cssMotion = async (name, selector) => {
    const running = await page.locator(selector).evaluateAll((nodes) =>
      nodes.flatMap((node) =>
        node
          .getAnimations({ subtree: true })
          .filter((animation) => {
            const timing = animation.effect?.getComputedTiming();
            return (
              ["running", "pending"].includes(animation.playState) &&
              timing &&
              Number(timing.activeDuration) > 0
            );
          })
          .map((animation) => ({
            type: animation.constructor.name,
            target: animation.effect?.target?.className ?? "unknown",
            duration: animation.effect?.getComputedTiming().activeDuration,
          })),
      ),
    );
    motion.css.push({ name, running });
    assert.deepEqual(running, [], `${name} has no active CSS motion with reduced motion enabled`);
  };
  const applyTheme = async (theme) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.evaluate(() => {
      localStorage.setItem("devfeed:theme", "system");
      window.dispatchEvent(
        new StorageEvent("storage", { key: "devfeed:theme", newValue: "system" }),
      );
    });
    await page.waitForFunction(
      (dark) => document.documentElement.classList.contains("dark") === dark,
      theme === "dark",
    );
  };

  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    motion.webAnimations = await observeAnimations(page, async () => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(route);
      await page.locator(selectors.cards).first().waitFor();
      const layout = page.getByRole("button", { name: /^Change feed layout(?::|$)/ });
      await tabTo(page, layout);
      await focused(page, layout);
      await page.keyboard.press("Enter");
      const menu = page.getByRole("menu");
      await menu.waitFor();
      assert.equal(
        await menu.getByRole("menuitemradio", { name: "Columns", exact: true }).count(),
        signedIn ? 1 : 0,
        "Columns is offered only to signed-in My feed readers",
      );
      await scan("keyboard-layout-menu", '[role="menu"]');
      await cssMotion("layout menu", '[role="menu"]');
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "hidden" });
      await focused(page, layout);
      interactions.push("layout selector is reachable by Tab and restores focus after Escape");

      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        if (width === 390) {
          touch = await page.context().newCDPSession(page);
          await touch.send("Emulation.setTouchEmulationEnabled", {
            enabled: true,
            maxTouchPoints: 1,
          });
          assert.equal(await page.evaluate(() => matchMedia("(pointer: coarse)").matches), true);
        }
        for (const theme of ["light", "dark"]) {
          await applyTheme(theme);
          for (const view of signedIn
            ? ["cards", "compact", "stacked", "columns"]
            : ["cards", "compact", "stacked"]) {
            await chooseInlineArticleView(page, view, { keyboard: true });
            await page.locator(selectors[view]).first().waitFor();
            assert.equal(
              await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
              true,
            );
            assert.ok(
              await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
              `${view} reflows without page-level horizontal overflow at ${width}px`,
            );
            if (width === 390) {
              const bookmark = page.locator(`${selectors[view]} .bookmark-button:visible`).first();
              const bounds = await bookmark.boundingBox();
              assert.ok(
                bounds?.width >= 44 && bounds.height >= 44,
                "Touch bookmarks retain 44px targets",
              );
            }
            const name = `${width}-${theme}-${view}`;
            if (width === 390 && theme === "dark" && view === "stacked") {
              const toast = page.locator(".settings-toast");
              await toast.getByText("Layout saved for this feed.", { exact: true }).waitFor();
              const geometry = await toast.evaluate((node) => {
                const viewport = node.closest(".settings-toast-viewport");
                const bounds = node.getBoundingClientRect();
                const navigation = document.querySelector(".mobile-nav").getBoundingClientRect();
                return {
                  position: getComputedStyle(viewport).position,
                  listStyle: getComputedStyle(viewport).listStyleType,
                  background: getComputedStyle(node).backgroundColor,
                  opacity: getComputedStyle(node).opacity,
                  bounds: bounds.toJSON(),
                  navigation: navigation.toJSON(),
                  viewport: { width: innerWidth, height: innerHeight },
                };
              });
              assert.equal(geometry.position, "fixed", "Save feedback floats over the reader");
              assert.equal(geometry.listStyle, "none", "Toasts do not render list numbering");
              assert.match(geometry.background, /^rgb\(/, "The toast has an opaque surface");
              assert.equal(geometry.opacity, "1");
              assert.ok(
                geometry.bounds.left >= 0 &&
                  geometry.bounds.right <= geometry.viewport.width &&
                  geometry.bounds.top >= 0 &&
                  geometry.bounds.bottom < geometry.navigation.top,
                "Mobile save feedback fits above the fixed navigation",
              );
              const dismiss = toast.getByRole("button", {
                name: "Dismiss notification",
                exact: true,
              });
              const dismissBounds = await dismiss.boundingBox();
              assert.ok(
                dismissBounds?.width >= 44 && dismissBounds.height >= 44,
                "Toast dismissal retains a 44px touch target",
              );
              proof.toast = geometry;
              await cssMotion("layout save toast", ".settings-toast-viewport");
              await scan("390-dark-layout-save-toast", ".settings-toast-viewport");
              await dismiss.focus();
              await page.keyboard.press("Enter");
              await toast.waitFor({ state: "hidden" });
              interactions.push(
                "layout save feedback stays above mobile navigation and dismisses by keyboard",
              );
            }
            await cssMotion(name, "main");
            await scan(name);
          }
        }
      }
      interactions.push("all supported views switch through the keyboard layout menu");
      if (!signedIn) {
        assert.equal(await workspace.count(), 0);
        return;
      }

      const add = workspace.getByRole("button", { name: "Add column", exact: true });
      await add.focus();
      await page.keyboard.press("Enter");
      const editor = page.getByRole("dialog", { name: "Add column", exact: true });
      await editor.waitFor();
      await editor.getByRole("combobox", { name: "Feed type", exact: true }).selectOption("topic");
      await editor.getByRole("button", { name: "Column Rust", exact: true }).waitFor();
      const first = editor.getByRole("combobox", { name: "Feed type", exact: true });
      const last = editor.getByRole("button", { name: "Cancel", exact: true });
      await last.focus();
      await page.keyboard.press("Tab");
      await focused(page, first);
      await page.keyboard.press("Shift+Tab");
      await focused(page, last);
      await cssMotion("column editor", "dialog[open]");
      await scan("390-dark-column-editor", "dialog[open]");
      await page.keyboard.press("Escape");
      await editor.waitFor({ state: "hidden" });
      await focused(page, add);
      interactions.push("column editor traps sequential focus and Escape restores Add column");

      const column = workspace.locator("[data-column-id]").first();
      const options = column.getByRole("button", { name: /^Options for .+ column$/ });
      const optionsMenu = await openColumnMenu(page, column, { keyboard: true });
      await cssMotion("column options", '[role="menu"]');
      await scan("390-dark-column-options", '[role="menu"]');
      await page.keyboard.press("Escape");
      await optionsMenu.waitFor({ state: "hidden" });
      await focused(page, options);
      const board = workspace.getByRole("region", { name: "Column feeds", exact: true });
      const scrollport = column.getByRole("region", {
        name: "Articles in Latest column",
        exact: true,
      });
      const articleLink = scrollport.locator(".article-stacked-title").first();
      assert.equal(await board.getAttribute("tabindex"), "-1");
      assert.equal(await scrollport.getAttribute("tabindex"), "-1");
      await scrollport.evaluate((node) => (node.scrollTop = 0));
      await tabTo(page, articleLink);
      await focused(page, articleLink);
      const verticalBefore = await scrollport.evaluate((node) => ({
        position: node.scrollTop,
        maximum: node.scrollHeight - node.clientHeight,
      }));
      const surroundingScroll = () =>
        scrollport.evaluate((node) => ({
          page: { x: scrollX, y: scrollY },
          board: node.closest('[aria-label="Column feeds"]').scrollLeft,
          siblings: [...document.querySelectorAll('[role="region"][aria-label^="Articles in "]')]
            .filter((other) => other !== node)
            .map((other) => ({ name: other.getAttribute("aria-label"), top: other.scrollTop })),
        }));
      const surroundingBefore = await surroundingScroll();
      assert.ok(verticalBefore.maximum > verticalBefore.position, "The article feed overflows");
      for (let index = 0; index < 8; index++) await page.keyboard.press("ArrowDown");
      await page.waitForFunction(({ node, before }) => node.scrollTop > before, {
        node: await scrollport.elementHandle(),
        before: verticalBefore.position,
      });
      await focused(page, articleLink);
      const verticalAfter = await scrollport.evaluate((node) => node.scrollTop);
      assert.deepEqual(
        await surroundingScroll(),
        surroundingBefore,
        "ArrowDown scrolls the active column without moving its siblings, board, or page",
      );
      interactions.push("Tab reaches an article link whose ArrowDown scrolls its own column");

      await board.evaluate((node) => (node.scrollLeft = 0));
      await tabTo(page, options);
      await focused(page, options);
      const horizontalBefore = await board.evaluate((node) => ({
        position: node.scrollLeft,
        maximum: node.scrollWidth - node.clientWidth,
      }));
      assert.ok(horizontalBefore.maximum > horizontalBefore.position, "The column board overflows");
      for (let index = 0; index < 8; index++) await page.keyboard.press("ArrowRight");
      await page.waitForFunction(({ node, before }) => node.scrollLeft > before, {
        node: await board.elementHandle(),
        before: horizontalBefore.position,
      });
      await focused(page, options);
      proof.descendantKeyboardScrolling = {
        vertical: { ...verticalBefore, after: verticalAfter, surroundings: surroundingBefore },
        horizontal: {
          ...horizontalBefore,
          after: await board.evaluate((node) => node.scrollLeft),
        },
      };
      interactions.push("Tab reaches column Options whose ArrowRight scrolls the narrow board");

      await board.evaluate((node) => (node.scrollLeft = 0));
      await board.focus();
      for (let index = 0; index < 8; index++) await page.keyboard.press("ArrowRight");
      await page.waitForFunction(
        () => document.querySelector('[aria-label="Column feeds"]').scrollLeft > 0,
      );
      interactions.push(
        "column options restore focus and the narrow board scrolls with ArrowRight",
      );

      fixture.setVisualArticles(null);
      fixture.pause();
      await page.reload();
      await workspace.getByText("Loading articles…", { exact: true }).first().waitFor();
      await cssMotion("column loading states", "main");
      await scan("390-dark-columns-loading");
      fixture.resume();
      // Finish every outgoing column load before arming the next generation's failure.
      await Promise.all(
        columnsFeedSelections
          .slice(0, 3)
          .map(({ id }) =>
            workspace
              .locator(`[data-column-id="${id}"] .article-stacked-row[data-article-id]`)
              .first()
              .waitFor(),
          ),
      );
      fixture.setEmpty("latest", true);
      fixture.failNext("source");
      fixture.unavailable.add(columnsFeedSelections[2].feed.id);
      await page.reload();
      await workspace.getByText("No articles yet.", { exact: true }).waitFor();
      await workspace
        .getByText("Couldn’t load articles in this column.", { exact: true })
        .waitFor();
      await workspace.getByRole("heading", { name: "Unavailable topic", exact: true }).waitFor();
      await scan("390-dark-columns-partial-error-empty-unavailable");
      await cssMotion("column error and empty states", "main");
      fixture.setColumns([]);
      await page.reload();
      await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
      await scan("390-dark-columns-empty-workspace");
      interactions.push("empty, failed, and unavailable columns remain accessible independently");
    });
    assert.deepEqual(motion.webAnimations, [], "Reduced motion prevents reader Web Animations");
    for (const { name, result } of axeResults)
      assertAccessible(result, `Saved reader views/${signedIn ? "member" : "guest"}/${name}`);
    await writeReaderViewReport(output, { status: "passed", ...proof });
  } catch (error) {
    await page
      .screenshot({ path: join(output, "failure.png"), animations: "disabled" })
      .catch(() => {});
    await writeReaderViewReport(output, {
      status: "failed",
      ...proof,
      error: String(error.stack ?? error),
    });
    throw error;
  } finally {
    if (touch) {
      await touch.send("Emulation.setTouchEmulationEnabled", { enabled: false }).catch(() => {});
      await touch.detach();
    }
    await fixture.cleanup();
    if (originalViewport) await page.setViewportSize(originalViewport);
  }
}
