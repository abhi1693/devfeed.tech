import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "../../apps/web/tests/browser/accessibility.mjs";

export function readingStreakFixture() {
  const today = new Date().toISOString().slice(0, 10);
  const date = (offset) =>
    new Date(Date.parse(`${today}T12:00:00Z`) + offset * 86_400_000).toISOString().slice(0, 10);
  const states = {
    active: { current: 2, best: 11, total: 24, last: 0, counts: [0, 3, 1, 0, 0, 1, 2] },
    milestone: { current: 11, best: 11, total: 36, last: 0, counts: [1, 2, 1, 3, 1, 2, 1] },
    pending: { current: 2, best: 11, total: 24, last: -1, counts: [0, 3, 1, 0, 1, 2, 0] },
    returning: { current: 0, best: 11, total: 24, last: -3, counts: [2, 1, 3, 2, 0, 0, 0] },
    new: { current: 0, best: 0, total: 0, last: null, counts: [0, 0, 0, 0, 0, 0, 0] },
  };
  return {
    state: "active",
    requests: 0,
    profile() {
      const state = states[this.state];
      assert.ok(state, `Unknown reading fixture state: ${this.state}`);
      return {
        current_days: state.current,
        longest_days: state.best,
        total_days: state.total,
        last_read_date: state.last === null ? null : date(state.last),
      };
    },
    week() {
      const state = states[this.state];
      assert.ok(state, `Unknown reading fixture state: ${this.state}`);
      return {
        today,
        timezone: "UTC",
        days: state.counts.map((article_count, index) => ({
          date: date(index - 6),
          article_count,
        })),
      };
    },
    response() {
      this.requests++;
      return this.week();
    },
  };
}

async function checkWeek(panel, fixture) {
  const week = panel.getByRole("region", { name: "Your week", exact: true });
  await week.getByRole("listitem").first().waitFor();
  const expected = fixture.week();
  const days = week.getByRole("listitem");
  assert.equal(await days.count(), 7, "The week includes exactly seven real calendar days");
  assert.deepEqual(
    await days.locator("time").evaluateAll((nodes) => nodes.map((node) => node.dateTime)),
    expected.days.map((day) => day.date),
    "Weekly tiles retain the API's chronological UTC dates",
  );
  for (const [index, day] of expected.days.entries()) {
    const item = days.nth(index);
    assert.equal(
      await item.getAttribute("data-state"),
      day.article_count > 0 ? "read" : day.date === expected.today ? "pending" : "missed",
      `The state of ${day.date} comes from its recorded reading count`,
    );
    const label = await item.getAttribute("aria-label");
    assert.ok(label, "Weekly states have an accessible date and reading count");
    assert.match(label, new RegExp(`\\b${day.article_count} articles? opened`));
  }
}

async function closeWithEscape(page, panel, trigger) {
  const before = await page.evaluate(() => ({
    tag: document.activeElement?.tagName,
    label: document.activeElement?.getAttribute("aria-label"),
    focused: document.hasFocus(),
    visible: document.visibilityState,
  }));
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  try {
    await page.waitForFunction(
      (node) => node === document.activeElement,
      await trigger.elementHandle(),
      { timeout: 5000 },
    );
  } catch {
    const after = await page.evaluate(() => ({
      tag: document.activeElement?.tagName,
      label: document.activeElement?.getAttribute("aria-label"),
      focused: document.hasFocus(),
      visible: document.visibilityState,
      trigger: document.querySelector(".reading-streak-trigger")?.getAttribute("aria-label"),
    }));
    assert.fail(`Escape must restore streak-trigger focus: ${JSON.stringify({ before, after })}`);
  }
}

export async function checkReadingStreak(page, screenshot, { fixture, mustReads } = {}) {
  assert.ok(fixture, "Reading streak checks require recorded weekly fixture data");
  await mkdir(path.dirname(screenshot), { recursive: true });
  const original = page.viewportSize();
  const originalTheme = await page.locator("html").getAttribute("class");
  const originalMotion = await page.evaluate(() =>
    matchMedia("(prefers-reduced-motion: reduce)").matches ? "reduce" : "no-preference",
  );
  const trigger = page.getByRole("button", { name: "Reading streak: 2 days", exact: true });
  const panel = page.getByRole("dialog", { name: "Your reading streak", exact: true });
  await trigger.waitFor();
  assert.equal(fixture.requests, 0, "The header does not fetch weekly reading history before open");
  try {
    const response = page.waitForResponse((value) =>
      new URL(value.url()).pathname.endsWith("/user/settings/reading-week"),
    );
    await trigger.focus();
    await page.keyboard.press("Enter");
    await panel.waitFor();
    assert.equal((await response).status(), 200);
    await checkWeek(panel, fixture);
    assert.equal(await panel.getByText("Best: 11 days", { exact: true }).count(), 1);
    assert.equal(
      await panel.getByRole("region", { name: "Next milestone", exact: true }).count(),
      1,
    );
    await closeWithEscape(page, panel, trigger);

    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const theme of ["light", "dark"]) {
      await page.locator("html").evaluate((node, value) => {
        node.classList.remove("light", "dark");
        node.classList.add(value);
      }, theme);
      for (const width of [1440, 375, 320]) {
        const height = width === 1440 ? 1000 : 844;
        await page.setViewportSize({ width, height });
        await trigger.click();
        await panel.waitFor();
        await checkWeek(panel, fixture);
        const bounds = await panel.boundingBox();
        assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width, `Popover fits ${width}px`);
        assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= height, "Popover fits vertically");
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          false,
          "The streak panel does not introduce horizontal page scrolling",
        );
        await page.waitForFunction(
          (node) =>
            node
              .getAnimations({ subtree: true })
              .every((animation) => animation.playState !== "running"),
          await panel.elementHandle(),
        );
        await page.screenshot({
          path: `${screenshot}-${theme}-${width}.png`,
          animations: "disabled",
        });
        await closeWithEscape(page, panel, trigger);
        if (width !== 375) {
          // Axe's frame/focus probes can dismiss non-modal popovers in Edge.
          // Exercise keyboard behavior before scanning a fresh popover.
          await trigger.click();
          await panel.waitFor();
          const result = await new AxeBuilder({ page })
            .include(".reading-streak-panel")
            .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
            .analyze();
          await writeFile(
            `${screenshot}-${theme}-${width}-axe.json`,
            JSON.stringify(result, null, 2),
          );
          assertAccessible(result, `Reading streak ${theme} at ${width}px`);
          await page.keyboard.press("Escape");
          await panel.waitFor({ state: "hidden" });
        }
      }
    }

    await page.setViewportSize({ width: 640, height: 1000 });
    const originalFont = await page.locator("html").evaluate((node) => node.style.fontSize);
    try {
      await page.locator("html").evaluate((node) => {
        node.style.fontSize = `${parseFloat(getComputedStyle(node).fontSize) * 2}px`;
      });
      await trigger.click();
      await panel.waitFor();
      await checkWeek(panel, fixture);
      const bounds = await panel.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 640, "200% text fits the viewport");
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
        "Enlarged streak text does not introduce horizontal page scrolling",
      );
      const close = panel.getByRole("button", { name: "Close reading streak", exact: true });
      assert.ok(await close.isVisible(), "The close control remains available with enlarged text");
      await panel.screenshot({
        path: `${screenshot}-200-percent-text.png`,
        animations: "disabled",
      });
      await closeWithEscape(page, panel, trigger);
    } finally {
      await page.locator("html").evaluate((node, font) => {
        node.style.fontSize = font;
      }, originalFont);
    }

    for (const state of ["milestone", "pending", "returning", "new"]) {
      fixture.state = state;
      await page.setViewportSize({ width: state === "returning" ? 320 : 375, height: 844 });
      await page.reload();
      const current = fixture.profile().current_days;
      const stateTrigger = page.getByRole("button", {
        name: `Reading streak: ${current} ${current === 1 ? "day" : "days"}`,
        exact: true,
      });
      await stateTrigger.click();
      await panel.waitFor();
      await checkWeek(panel, fixture);
      if (state === "milestone") {
        assert.equal(await panel.getByText("Personal best", { exact: true }).count(), 1);
        const progress = panel.getByRole("progressbar", {
          name: "Progress to next streak milestone",
          exact: true,
        });
        assert.equal(await progress.getAttribute("aria-valuenow"), "11");
        assert.equal(await progress.getAttribute("aria-valuemax"), "14");
      } else if (state === "returning") {
        assert.equal(await panel.getByRole("heading", { name: "Start a new streak" }).count(), 1);
        assert.equal(
          await panel.getByText("Best: 11 days", { exact: true }).count(),
          1,
          "A missed day keeps the reader's earned personal best",
        );
      } else if (state === "new") {
        assert.equal(await panel.getByRole("region", { name: "Milestones earned" }).count(), 0);
        assert.equal(await panel.getByRole("heading", { name: "Start your streak" }).count(), 1);
      }
      await panel.screenshot({ path: `${screenshot}-${state}.png`, animations: "disabled" });
      if (state === "milestone") {
        await page.setViewportSize({ width: 1440, height: 1000 });
        for (const theme of ["light", "dark"]) {
          await page.locator("html").evaluate((node, value) => {
            node.classList.remove("light", "dark");
            node.classList.add(value);
          }, theme);
          await panel.screenshot({
            path: `${screenshot}-milestone-${theme}-desktop.png`,
            animations: "disabled",
          });
        }
      }
      await closeWithEscape(page, panel, stateTrigger);
    }

    if (mustReads) {
      fixture.state = "pending";
      mustReads.enabled = true;
      mustReads.presented = true;
      await page.reload();
      await trigger.click();
      await panel.waitFor();
      await checkWeek(panel, fixture);
      await panel.getByRole("button", { name: "Find today’s read", exact: true }).click();
      await panel.waitFor({ state: "hidden" });
      const briefing = page.getByRole("dialog", { name: "Today’s Must Reads", exact: true });
      await briefing.waitFor();
      assert.equal(
        await briefing.getByRole("link", { name: "Daily Must Read 1", exact: true }).count(),
        1,
      );
      await briefing.getByRole("button", { name: "Close Must Reads", exact: true }).click();
    }
  } finally {
    fixture.state = "active";
    if (mustReads) mustReads.enabled = false;
    await page.setViewportSize(original);
    await page.emulateMedia({ reducedMotion: originalMotion });
    await page.reload();
    await trigger.waitFor();
    await page.locator("html").evaluate((node, value) => {
      if (value === null) node.removeAttribute("class");
      else node.setAttribute("class", value);
    }, originalTheme);
  }
}
