import assert from "node:assert/strict";

export const leaderboardProfile = {
  username: "leader-reader",
  display_name: "Leading Reader",
  avatar_url: null,
  bio: "Reading every day.",
};

const rows = Array.from({ length: 10 }, (_, index) => ({
  rank: index < 2 ? 1 : index + 1,
  username: index ? `reader-${index}` : leaderboardProfile.username,
  display_name:
    index === 9
      ? "A reader with a very long display name"
      : index
        ? `Reader ${index}`
        : leaderboardProfile.display_name,
  avatar_url: null,
  days: index < 2 ? 365 : 365 - index * 10,
}));
const boards = {
  longest_streak: rows,
  reading_days: rows.map((row) => ({ ...row, days: row.days + 100 })),
};

export async function checkLeaderboard(page, prefix, { signedIn = false } = {}) {
  const previousUrl = page.url();
  const viewport = page.viewportSize();
  const reducedMotion = await page.evaluate(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const extension = previousUrl.startsWith("chrome-extension:");
  const base = extension ? previousUrl.split("#")[0] + "#" : new URL(previousUrl).origin;
  let mode = "normal";
  let ownReads = 0;
  const sparseBoards = {
    longest_streak: [boards.longest_streak[0]],
    reading_days: [boards.reading_days[0]],
  };
  const mixedBoards = {
    ...boards,
    reading_days: boards.reading_days.map((row) => ({ ...row, rank: 1, days: 465 })),
  };
  const publicRoute = async (route) =>
    route.fulfill({
      status: mode === "public-error" ? 503 : 200,
      json:
        mode === "empty"
          ? { longest_streak: [], reading_days: [] }
          : mode === "self-only"
            ? sparseBoards
            : mode === "mixed"
              ? mixedBoards
              : boards,
    });
  const ownRoute = async (route) => {
    ownReads++;
    return route.fulfill({
      status: mode === "own-error" ? 503 : 200,
      json: ["unclaimed", "empty"].includes(mode)
        ? { longest_streak: null, reading_days: null }
        : mode === "self-only"
          ? {
              longest_streak: sparseBoards.longest_streak[0],
              reading_days: sparseBoards.reading_days[0],
            }
          : mode === "mixed"
            ? {
                longest_streak: boards.longest_streak[9],
                reading_days: {
                  ...mixedBoards.reading_days[0],
                  username: "own-reader",
                  display_name: "Your Reader",
                },
              }
            : {
                longest_streak: {
                  ...rows[0],
                  username: "own-reader",
                  display_name: "Your Reader",
                  rank: 1521,
                  days: 12,
                },
                reading_days: {
                  ...rows[0],
                  username: "own-reader",
                  display_name: "Your Reader",
                  rank: 1420,
                  days: 24,
                },
              },
    });
  };
  const profileRoute = (route) =>
    route.fulfill({ json: { profile: leaderboardProfile, activity: null } });
  await page.route("**/api/v1/leaderboard", publicRoute);
  await page.route("**/api/v1/user/leaderboard/me", ownRoute);
  await page.route("**/api/v1/users/leader-reader", profileRoute);
  try {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.locator(".sidebar").getByRole("link", { name: "Leaderboard", exact: true }).click();
    await page.getByRole("heading", { name: "Leaderboard", exact: true }).waitFor();
    assert.equal(await page.getByRole("navigation", { name: "Breadcrumb" }).count(), 0);
    const streaks = page.getByRole("region", { name: "Longest streak", exact: true });
    const days = page.getByRole("region", { name: "Most reading days", exact: true });
    await streaks
      .getByRole("link", { name: rankingName("Leading Reader", 1, 365, false), exact: true })
      .waitFor();
    assert.equal(await streaks.getByRole("listitem").count(), 10);
    assert.equal(await days.getByRole("listitem").count(), 10);
    const headingIcons = page.locator("main h1 svg, main h2 svg");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    assert.ok(
      (
        await headingIcons.evaluateAll((icons) =>
          icons.map((icon) => getComputedStyle(icon).animationName),
        )
      ).every((name) => name !== "none"),
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.ok(
      (
        await headingIcons.evaluateAll((icons) =>
          icons.map((icon) => getComputedStyle(icon).animationName),
        )
      ).every((name) => name === "none"),
    );
    await page.emulateMedia({ reducedMotion: reducedMotion ? "reduce" : "no-preference" });
    assert.equal(
      await streaks
        .getByRole("link", { name: rankingName("Reader 1", 1, 365, false), exact: true })
        .count(),
      1,
    );
    assert.equal(
      await streaks
        .getByRole("link", { name: rankingName("Reader 2", 3, 345, false), exact: true })
        .count(),
      1,
    );
    if (signedIn) {
      await streaks
        .getByRole("link", { name: rankingName("Your Reader", 1521, 12, true), exact: true })
        .waitFor();
      await days
        .getByRole("link", { name: rankingName("Your Reader", 1420, 24, true), exact: true })
        .waitFor();
      await page.getByRole("button", { name: /^User menu:/ }).click();
      const menuLink = page.getByRole("menuitem", { name: "Leaderboard", exact: true });
      assert.equal(await menuLink.getAttribute("href"), `${extension ? "#" : ""}/leaderboard`);
      await page.keyboard.press("Escape");
    } else {
      await streaks.getByRole("link", { name: "Sign in to join" }).waitFor();
      assert.equal(ownReads, 0);
    }
    const dark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
    for (const theme of ["light", "dark"]) {
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value === "dark"),
        theme,
      );
      await page.screenshot({ path: `${prefix}-desktop-${theme}.png`, fullPage: true });
    }
    await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), dark);
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      assert.equal(
        await page.locator(".mobile-nav").getByRole("link", { name: "Leaderboard" }).count(),
        0,
        "Rankings are omitted from the compact mobile navigation",
      );
      await page.screenshot({ path: `${prefix}-${width}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    await streaks
      .getByRole("link", { name: rankingName("Leading Reader", 1, 365, false), exact: true })
      .click();
    await page.waitForURL(`**${extension ? "#" : ""}/users/leader-reader`);
    assert.ok(page.url().endsWith(`${extension ? "#" : ""}/users/leader-reader`));
    await page.getByRole("heading", { name: "Leading Reader", exact: true }).waitFor();
    await page.goto(`${base}/leaderboard`);
    await streaks.getByRole("listitem").first().waitFor();
    if (signedIn) {
      mode = "self-only";
      await page.reload();
      await streaks
        .getByRole("link", { name: rankingName("Leading Reader", 1, 365, true), exact: true })
        .waitFor();
      await days
        .getByRole("link", { name: rankingName("Leading Reader", 1, 465, true), exact: true })
        .waitFor();
      for (const board of [streaks, days]) {
        assert.equal(await board.getByRole("listitem").count(), 1);
        assert.equal(await board.locator('a[data-own="true"]').count(), 1);
        assert.equal(await board.locator("footer").count(), 0);
      }
      await page.screenshot({ path: `${prefix}-self-only-desktop.png`, fullPage: true });
      await page.setViewportSize({ width: 320, height: 844 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      await page.screenshot({ path: `${prefix}-self-only-320.png`, fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
      mode = "mixed";
      await page.reload();
      await streaks
        .getByRole("link", {
          name: rankingName("A reader with a very long display name", 10, 275, true),
          exact: true,
        })
        .waitFor();
      await days
        .getByRole("link", { name: rankingName("Your Reader", 1, 465, true), exact: true })
        .waitFor();
      assert.equal(await streaks.getByRole("listitem").count(), 10);
      assert.equal(await days.getByRole("listitem").count(), 10);
      assert.equal(await streaks.locator('a[data-own="true"]').count(), 1);
      assert.equal(await streaks.locator("footer").count(), 0);
      assert.equal(await days.locator('footer a[data-own="true"]').count(), 1);
      assert.equal(await days.locator('ol a[data-own="true"]').count(), 0);
    }
    mode = "public-error";
    await page.reload();
    // Restoring the account replaces the guest subtree. Wait for that remount
    // before changing the fixture, or its second request can remove the retry
    // button before Playwright clicks it.
    if (signedIn) await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    await page.getByRole("heading", { name: "Leaderboard unavailable" }).waitFor();
    mode = "empty";
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    await streaks.getByText("No rankings yet.").waitFor();
    await page.screenshot({ path: `${prefix}-empty.png`, fullPage: true });
    if (signedIn) {
      mode = "own-error";
      await page.reload();
      await streaks.getByRole("button", { name: "Retry your ranking", exact: true }).waitFor();
      assert.equal(await streaks.getByRole("listitem").count(), 10);
      mode = "unclaimed";
      await streaks.getByRole("button", { name: "Retry your ranking" }).click();
      await streaks.getByRole("link", { name: "Claim your username", exact: true }).waitFor();
    }
  } finally {
    await page.unroute("**/api/v1/leaderboard", publicRoute);
    await page.unroute("**/api/v1/user/leaderboard/me", ownRoute);
    await page.unroute("**/api/v1/users/leader-reader", profileRoute);
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: reducedMotion ? "reduce" : "no-preference" });
    await page.goto(previousUrl);
  }
}

function rankingName(name, rank, days, own) {
  return new RegExp(
    `^#${rank.toLocaleString("en-US")}\\s*${name}${own ? "\\s*You" : ""}\\s*@.*${days}\\s*days$`,
  );
}
