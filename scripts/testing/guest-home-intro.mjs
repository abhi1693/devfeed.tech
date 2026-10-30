import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const heading = "Developer news, tutorials & releases";

/** The same introduction and live transitions are checked in each built reader. */
export async function checkGuestHomeIntro(page, destination, fixture, output, extension = false) {
  await mkdir(output, { recursive: true });
  fixture.active = true;
  fixture.signedIn = false;
  await page.addInitScript(() => {
    if (location.protocol !== "about:") localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  const intro = page.getByRole("region", { name: heading, exact: true });
  const measurements = [];
  const unvisited = [];
  const record = (request) => {
    const url = new URL(request.url());
    if (["/topics", "/sources", "/mcp"].includes(url.pathname)) unvisited.push(request.url());
  };
  const logout = async (route) => {
    assert.equal(route.request().method(), "POST");
    assert.equal(route.request().headers()["x-csrf-token"], "test");
    fixture.signedIn = false;
    if (!extension) await page.context().clearCookies();
    return route.fulfill({ json: { url: "https://identity.example/signed-out" } });
  };
  await page.context().route("**/api/v1/user/auth/sso/logout", logout);
  await page
    .context()
    .route("https://identity.example/signed-out", (route) =>
      route.fulfill({ contentType: "text/html", body: "<p>Signed out</p>" }),
    );
  try {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("about:blank");
      fixture.requests.length = 0;
      unvisited.length = 0;
      page.on("request", record);
      await page.goto(destination("/?utm_source=introduction-check"));
      await intro.waitFor();
      await page.locator(".article-card").first().waitFor();
      assert.equal(await page.getByRole("heading", { level: 1 }).count(), 1);
      assert.equal(await intro.getByRole("heading", { name: heading, level: 1 }).count(), 1);
      for (const [name, path] of [
        ["topics", "/topics"],
        ["sources", "/sources"],
        ["MCP", "/mcp"],
      ])
        assert.equal(
          await intro.getByRole("link", { name, exact: true }).getAttribute("href"),
          extension ? `#${path}` : path,
        );
      const box = await intro.boundingBox();
      const card = await page.locator(".article-card").first().boundingBox();
      assert.ok(box.height < (width < 500 ? 300 : 220), "introduction stays compact");
      assert.ok(card.y < 700, "the first article remains near the top");
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      assert.equal(fixture.requests.filter((url) => url.pathname.endsWith("/feed")).length, 1);
      assert.deepEqual(unvisited, [], "intro links do not prefetch directories or MCP");
      page.off("request", record);
      measurements.push({
        width,
        introHeight: box.height,
        firstArticleTop: card.y,
        publicFeedRequests: 1,
        navigation: await page.evaluate(() => {
          const entry = performance.getEntriesByType("navigation")[0];
          return {
            responseStart: entry.responseStart,
            domContentLoaded: entry.domContentLoadedEventEnd,
          };
        }),
      });
      await page.screenshot({ path: `${output}/guest-${width}.png`, animations: "disabled" });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(destination("/"));
    await page.locator(".feed-toolbar").getByRole("link", { name: "News", exact: true }).click();
    await page.waitForURL(destination("/news"));
    await page.locator(".article-card").first().waitFor();
    assert.equal(await intro.count(), 0, "selecting a content tab removes the introduction");
    await page.locator(".feed-toolbar").getByRole("link", { name: "All", exact: true }).click();
    await page.waitForURL(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    assert.equal(await intro.count(), 0, "Latest remains a feed without repeated home copy");
    await page.getByRole("link", { name: "DevFeed home", exact: true }).click();
    await intro.waitFor();
    await intro.getByRole("link", { name: "topics", exact: true }).click();
    await page.waitForURL(destination("/topics"));
    assert.equal(await intro.count(), 0);
    await page.goBack();
    await intro.waitFor();
    await page.locator(".article-card").nth(8).scrollIntoViewIfNeeded();
    const scroll = await page.evaluate(() => scrollY);
    await page.locator(".card-open-link").nth(8).click();
    const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
    await preview.waitFor();
    await intro.waitFor({ state: "hidden" });
    await preview.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.waitForURL(destination("/"));
    await intro.waitFor();
    await preview.waitFor({ state: "detached" });
    assert.ok(
      Math.abs((await page.evaluate(() => scrollY)) - scroll) < 3,
      "closing an article preserves the original feed scroll position",
    );
    for (const path of [
      "/?q=compiler",
      "/?topic=javascript",
      "/?tag=typescript",
      "/?content_type=news",
      "/?source_id=33333333-3333-4333-8333-333333333333",
      "/?sort=oldest",
      "/?cursor=next",
      "/latest",
      "/search?q=compiler",
      `/articles/${fixture.articles[0].slug}`,
    ]) {
      await page.goto(destination(path));
      if (path.startsWith("/articles/")) await preview.waitFor();
      else if (!path.startsWith("/search")) await page.locator(".article-card").first().waitFor();
      assert.equal(await intro.count(), 0, `${path} has no repeated guest introduction`);
    }
    fixture.signedIn = true;
    if (!extension)
      await page
        .context()
        .addCookies([{ name: "devfeed_user_session", value: "valid", url: destination("/") }]);
    await page.goto("about:blank");
    await page.goto(destination("/"));
    await page.getByRole("button", { name: /User menu:/ }).waitFor();
    await page.locator(".article-card").first().waitFor();
    assert.equal(await intro.count(), 0, "My feed has no guest introduction");
    assert.ok(fixture.requests.some((url) => url.pathname.endsWith("/user/feed")));
    await page.getByRole("button", { name: /User menu:/ }).click();
    if (extension) {
      const popup = page.context().waitForEvent("page");
      await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
      const provider = await popup;
      await provider.waitForURL("https://identity.example/signed-out");
      await provider.close();
    } else {
      await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
      await page.waitForURL("https://identity.example/signed-out");
      await page.goto(destination("/"));
    }
    await intro.waitFor();
    await page.locator(".article-card").first().waitFor();
    assert.equal(await page.getByRole("button", { name: /User menu:/ }).count(), 0);
    if (!extension) {
      const response = await page.context().request.get(destination("/"));
      assert.match(response.headers()["cache-control"], /private/);
      const html = await response.text();
      assert.ok(html.includes('id="guest-home-title"'));
      assert.ok(
        !html.includes("reader@example.test") && !html.includes("feed-mode-reader"),
        "anonymous HTML contains no account identity from the previous session",
      );
    }
    await writeFile(`${output}/measurements.json`, JSON.stringify(measurements, null, 2));
    console.log(
      "Guest introduction: compact layouts, no speculative reads, filters, previews and account transitions passed.",
      measurements,
    );
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    throw error;
  } finally {
    page.off("request", record);
    fixture.active = false;
    fixture.signedIn = false;
    await page.context().unroute("**/api/v1/user/auth/sso/logout", logout);
  }
}
