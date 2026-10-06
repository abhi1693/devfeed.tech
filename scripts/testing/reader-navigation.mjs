import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "../../apps/web/tests/browser/accessibility.mjs";

const widths = [320, 360, 375, 390, 430, 520, 768, 801, 1440];
const tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function measure(page) {
  return page.evaluate(() => {
    const visible = (element) => element && element.getBoundingClientRect().width > 0;
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      return {
        name: element.getAttribute("aria-label") || element.textContent.trim(),
        x: box.x,
        y: box.y,
        width: box.width,
        height: box.height,
        right: box.right,
        bottom: box.bottom,
      };
    };
    const footer = document.querySelector(".mobile-nav");
    return {
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      sidebar: document.documentElement.dataset.sidebarState,
      brand: rect(document.querySelector(".topbar .brand")),
      wordmark: visible(document.querySelector(".topbar .brand > span"))
        ? rect(document.querySelector(".topbar .brand > span"))
        : null,
      search: rect(document.querySelector(".topbar .search")),
      actions: [...document.querySelectorAll(".header-actions button, .header-actions a")]
        .filter(visible)
        .map(rect),
      footer: visible(footer) ? rect(footer) : null,
      mainBottomPadding: Number.parseFloat(
        getComputedStyle(document.querySelector(".main-content")).paddingBottom,
      ),
      links: [...footer.querySelectorAll("a")].filter(visible).map(rect),
    };
  });
}

function defects(snapshot, signedIn) {
  const issues = [];
  const { width, brand, wordmark, search, actions, footer, links } = snapshot;
  const overlap = (left, right) =>
    Math.min(left.right, right.right) > Math.max(left.x, right.x) + 1 &&
    Math.min(left.bottom, right.bottom) > Math.max(left.y, right.y) + 1;
  if (snapshot.scrollWidth > width + 1) issues.push("page scrolls horizontally");
  if (wordmark) {
    for (const action of actions) {
      if (overlap(wordmark, action)) issues.push(`brand wordmark: overlaps ${action.name}`);
    }
  }
  const header = [brand, ...actions];
  for (const [index, item] of header.entries()) {
    if (item.x < -1 || item.right > width + 1) issues.push(`${item.name}: outside viewport`);
    if (width <= 520 && (item.width < 43.5 || item.height < 43.5)) {
      issues.push(`${item.name}: header target smaller than 44px`);
    }
    for (const other of header.slice(index + 1)) {
      if (overlap(item, other)) issues.push(`${item.name}: overlaps ${other.name}`);
    }
    if (width <= 800 && overlap(item, search)) issues.push(`${item.name}: overlaps search`);
  }
  const actionCount = width <= 800 ? 4 : 5;
  if (signedIn && actions.length !== actionCount)
    issues.push(`expected ${actionCount} header actions, got ${actions.length}`);
  if (width <= 520 && wordmark) issues.push("phone header still shows the wordmark");
  if (width <= 520 && (search.x < -1 || search.right > width + 1 || search.height < 41.5)) {
    issues.push("mobile search does not fit its own usable row");
  }
  if (width <= 800) {
    if (!footer) issues.push("mobile navigation is hidden");
    if (footer && snapshot.mainBottomPadding < footer.height)
      issues.push("fixed navigation covers the end of the main content");
    if (links.length !== (signedIn ? 5 : 3)) {
      issues.push(`expected ${signedIn ? 5 : 3} mobile links, got ${links.length}`);
    }
    for (const [index, link] of links.entries()) {
      if (link.width < 43.5 || link.height < 43.5)
        issues.push(`${link.name}: footer target smaller than 44px`);
      if (link.x < -1 || link.right > width + 1)
        issues.push(`${link.name}: footer outside viewport`);
      for (const other of links.slice(index + 1)) {
        if (overlap(link, other)) issues.push(`${link.name}: footer overlaps ${other.name}`);
      }
    }
  } else if (footer) issues.push("mobile navigation appears on desktop");
  return issues;
}

export async function checkAvatarStreak(page, directory, { current, next, scan = false } = {}) {
  await mkdir(directory, { recursive: true });
  const avatar = page.getByRole("button", { name: /^User menu:/ });
  await avatar.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu");
  await menu.waitFor();
  const summary = menu.locator(".user-menu-reading-streak");
  const bar = summary.locator(".reading-streak-progress");
  await bar.waitFor();
  assert.equal(await bar.getAttribute("aria-hidden"), "true");
  assert.equal(
    await summary.getByRole("progressbar").count(),
    0,
    "A menu does not own an incompatible progressbar role",
  );
  const remaining = next - current;
  const explanation = summary.getByText(
    `${remaining} ${remaining === 1 ? "day" : "days"} to the next milestone of ${next} days.`,
    { exact: true },
  );
  assert.equal(
    await explanation.count(),
    1,
    "The progress information is available as accessible text",
  );
  assert.equal(await explanation.getAttribute("aria-hidden"), null);
  const progress = await bar
    .locator("span")
    .evaluate((node) => Number.parseFloat(node.style.width));
  assert.ok(
    Math.abs(progress - (current / next) * 100) < 0.001,
    "The visual bar represents the actual streak toward its next milestone",
  );
  assert.equal(
    await summary
      .getByText(`${current} ${current === 1 ? "day" : "days"}`, { exact: true })
      .count(),
    1,
  );
  assert.equal(
    await summary.getByRole("menuitem").count(),
    0,
    "The streak summary is information, not a navigation item",
  );
  assert.equal(
    await menu.getByRole("menuitem", { name: "Leaderboard", exact: true }).count(),
    0,
    "Rankings are omitted from the mobile avatar menu",
  );
  assert.equal(
    await page
      .locator(".header-actions")
      .getByRole("button", { name: /^Reading streak:/ })
      .count(),
    0,
  );
  const bounds = await menu.boundingBox();
  const viewport = page.viewportSize();
  assert.ok(
    bounds.x >= -1 && bounds.x + bounds.width <= viewport.width + 1,
    "Avatar menu fits horizontally",
  );
  assert.ok(
    bounds.y >= -1 && bounds.y + bounds.height <= viewport.height + 1,
    "Avatar menu fits vertically",
  );
  await page.waitForFunction(
    (node) =>
      node.getAnimations({ subtree: true }).every((animation) => animation.playState !== "running"),
    await menu.elementHandle(),
  );
  await page.screenshot({
    path: path.join(directory, `avatar-streak-${current}-${viewport.width}.png`),
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await menu.waitFor({ state: "hidden" });
  await page.waitForFunction(
    (node) => node === document.activeElement,
    await avatar.elementHandle(),
  );
  if (scan) {
    await avatar.click();
    await menu.waitFor();
    const result = await new AxeBuilder({ page })
      .include(".user-menu-content")
      .withTags(tags)
      .analyze();
    await writeFile(
      path.join(directory, `avatar-streak-${current}-${viewport.width}-axe.json`),
      JSON.stringify(result, null, 2),
    );
    assertAccessible(result, `Avatar streak ${current} days at ${viewport.width}px`);
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });
  }
}

/** Check real layout and navigation in the web reader and each built extension. */
export async function checkReaderNavigation(
  page,
  directory,
  { signedIn = false, baseline = false, geometryOnly = false, streakDays = 2 } = {},
) {
  await mkdir(directory, { recursive: true });
  const original = {
    viewport: page.viewportSize(),
    url: page.url(),
    dark: await page.evaluate(() => document.documentElement.classList.contains("dark")),
    sidebar: await page.evaluate(() => localStorage.getItem("devfeed:sidebar-expanded")),
    motion: await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
  };
  const state = signedIn ? `signed-in-${streakDays}` : "guest";
  const report = { state, baseline, measurements: [], scans: [], interactions: [] };
  const save = () =>
    writeFile(path.join(directory, `${state}.json`), JSON.stringify(report, null, 2));
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 900 });
    if (signedIn) {
      await page.getByRole("button", { name: /^User menu:/ }).waitFor();
      await page.getByRole("button", { name: "Today’s Must Reads", exact: true }).waitFor();
      await page.getByRole("button", { name: /^Notifications/ }).waitFor();
      await page
        .getByRole("button", { name: new RegExp(`^Reading streak: ${streakDays} days`) })
        .waitFor();
    } else {
      await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
      assert.equal(
        await page.locator(".sidebar").getByRole("link", { name: "My feed", exact: true }).count(),
        0,
      );
      assert.equal(
        await page
          .locator(".sidebar")
          .getByRole("link", { name: "Read later", exact: true })
          .count(),
        0,
      );
    }
    for (const expanded of [false, true]) {
      await page.evaluate((value) => {
        localStorage.setItem("devfeed:sidebar-expanded", String(value));
        window.dispatchEvent(new Event("devfeed:sidebar-state-change"));
      }, expanded);
      await page.waitForFunction(
        (value) => document.documentElement.dataset.sidebarState === value,
        expanded ? "expanded" : "collapsed",
      );
      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => {
          document.documentElement.classList.remove("light", "dark");
          document.documentElement.classList.add(value);
        }, theme);
        for (const width of widths) {
          await page.setViewportSize({ width, height: 900 });
          const snapshot = await measure(page);
          const issues = defects(snapshot, signedIn);
          report.measurements.push({ theme, expanded, ...snapshot, issues });
          if (width === 320 || (width === 1440 && !expanded)) {
            await page.screenshot({
              path: path.join(
                directory,
                `${state}-${theme}-${expanded ? "expanded" : "collapsed"}-${width}.png`,
              ),
              animations: "disabled",
            });
          }
        }
      }
    }
    await save();
    if (baseline) return report;
    const failures = report.measurements.filter((item) => item.issues.length);
    assert.deepEqual(
      failures.map(({ width, theme, expanded, issues }) => ({ width, theme, expanded, issues })),
      [],
      "Reader header and footer fit every supported width",
    );
    if (geometryOnly) return report;
    await page.setViewportSize({ width: 390, height: 900 });
    const nav = page.locator(".mobile-nav");
    const expected = signedIn
      ? ["My feed", "Latest", "Read later", "Topics", "Sources"]
      : ["Latest", "Topics", "Sources"];
    assert.deepEqual(
      await nav
        .getByRole("link")
        .allTextContents()
        .then((items) => items.map((item) => item.trim())),
      expected,
    );
    assert.equal(await nav.getByRole("link", { name: "Leaderboard" }).count(), 0);
    assert.equal(await nav.getByRole("link", { name: "Connect your agent" }).count(), 0);
    const extension = page.url().startsWith("chrome-extension:");
    const base = extension ? `${page.url().split("#")[0]}#` : new URL(page.url()).origin;
    for (const [name, route] of [
      ["Latest", "/latest"],
      ["Topics", "/topics"],
      ["Sources", "/sources"],
      ...(signedIn
        ? [
            ["Read later", "/read-later"],
            ["My feed", "/"],
          ]
        : []),
    ]) {
      const link = nav.getByRole("link", { name, exact: true });
      await link.focus();
      assert.equal(await link.evaluate((node) => document.activeElement === node), true);
      await page.keyboard.press("Enter");
      await page.waitForURL(`${base}${route}`);
      await nav.getByRole("link", { name, exact: true }).waitFor();
      assert.equal(
        await nav.getByRole("link", { name, exact: true }).getAttribute("aria-current"),
        "page",
        `${name} marks the current route`,
      );
      assert.equal(await nav.locator('[aria-current="page"]').count(), 1);
      report.interactions.push({ name: `${name} keyboard navigation`, status: "passed" });
    }
    await page.goto(original.url);
    await page.locator(".topbar .brand").waitFor();
    if (signedIn) {
      await page.getByRole("button", { name: /^User menu:/ }).waitFor();
      await page.getByRole("button", { name: "Today’s Must Reads", exact: true }).waitFor();
      await page.getByRole("button", { name: /^Notifications/ }).waitFor();
    } else {
      await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
    }
    for (const width of [320, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => {
          document.documentElement.classList.remove("light", "dark");
          document.documentElement.classList.add(value);
        }, theme);
        const scan = await new AxeBuilder({ page })
          .include(".topbar")
          .include(".mobile-nav")
          .withTags(tags)
          .analyze();
        await writeFile(
          path.join(directory, `${state}-axe-${theme}-${width}.json`),
          JSON.stringify(scan, null, 2),
        );
        assertAccessible(scan, `${state} navigation ${theme} ${width}`);
        report.scans.push({
          width,
          theme,
          violations: scan.violations.length,
          incomplete: scan.incomplete.length,
        });
        if (signedIn && width === 320) {
          await checkAvatarStreak(page, path.join(directory, `${state}-${theme}`), {
            current: streakDays,
            next: streakDays === 123 ? 150 : 3,
            scan: true,
          });
        } else if (signedIn) {
          await page.getByRole("button", { name: /^User menu:/ }).click();
          await page.getByRole("menu").waitFor();
          assert.equal(
            await page.locator(".user-menu-reading-streak").isVisible(),
            false,
            "Desktop retains the header streak entry",
          );
          await page.getByRole("menuitem", { name: "Leaderboard", exact: true }).waitFor();
          await page.keyboard.press("Escape");
        }
      }
    }
    await page.setViewportSize({ width: 320, height: 900 });
    await page.reload();
    await page.waitForFunction(() => document.documentElement.dataset.sidebarState === "expanded");
    if (signedIn) {
      await page.getByRole("button", { name: /^User menu:/ }).waitFor();
      await page.getByRole("button", { name: "Today’s Must Reads", exact: true }).waitFor();
      await page.getByRole("button", { name: /^Notifications/ }).waitFor();
    } else {
      await page.getByRole("link", { name: "Sign in", exact: true }).waitFor();
    }
    assert.deepEqual(
      defects(await measure(page), signedIn),
      [],
      "Remembered expanded sidebar still fits after mobile reload",
    );
    report.interactions.push({
      name: "persisted desktop sidebar preference on phone reload",
      status: "passed",
    });
    const originalFont = await page.locator("html").evaluate((node) => node.style.fontSize);
    await page.setViewportSize({ width: 640, height: 1000 });
    try {
      await page.locator("html").evaluate((node) => {
        node.style.fontSize = `${Number.parseFloat(getComputedStyle(node).fontSize) * 2}px`;
      });
      assert.deepEqual(
        defects(await measure(page), signedIn),
        [],
        "Navigation fits with doubled root font size",
      );
      if (signedIn) {
        await checkAvatarStreak(page, path.join(directory, `${state}-doubled-root-font`), {
          current: streakDays,
          next: streakDays === 123 ? 150 : 3,
        });
      }
      report.interactions.push({
        name: "Doubled root font size reflows navigation",
        status: "passed",
      });
    } finally {
      await page.locator("html").evaluate((node, font) => {
        node.style.fontSize = font;
      }, originalFont);
    }
  } finally {
    await save();
    await page.setViewportSize(original.viewport);
    await page.emulateMedia({ reducedMotion: original.motion ? "reduce" : "no-preference" });
    await page.evaluate(({ sidebar, dark }) => {
      if (sidebar === null) localStorage.removeItem("devfeed:sidebar-expanded");
      else localStorage.setItem("devfeed:sidebar-expanded", sidebar);
      window.dispatchEvent(new Event("devfeed:sidebar-state-change"));
      document.documentElement.classList.remove("light", "dark");
      document.documentElement.classList.add(dark ? "dark" : "light");
    }, original);
  }
}
