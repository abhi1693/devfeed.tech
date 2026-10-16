import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { feedModeFixture } from "./feed-mode.mjs";

/** Guest article/catalog data shared by production web and unpacked reader packages. */
export function loginDestinationsFixture(article) {
  const feed = feedModeFixture(article);
  return {
    active: false,
    articles: feed.articles,
    topic: feed.topic,
    source: feed.source,
    handles(url) {
      feed.active = this.active;
      return feed.handles(url);
    },
    respond(url, payload) {
      feed.signedIn = false;
      return feed.respond(url, payload);
    },
  };
}

/** Native sign-in links reach an isolated provider with a valid callback destination. */
export async function checkLoginDestinations(
  page,
  destination,
  fixture,
  output,
  { extension = false, provider = "https://identity.example/authorize" } = {},
) {
  await mkdir(output, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  fixture.active = true;
  const paths = [
    "/?content_type=news",
    "/latest?sort=most_liked&content_type=tutorial",
    "/search?q=reader&content_types=news&section=articles&sort=oldest",
    "/topics/javascript/tutorials?sort=most_liked",
    "/sources/publisher/news?sort=oldest",
    "/users/reader",
    `/mcp/authorize?request=${"a".repeat(43)}`,
  ];
  const providerUrl = new URL(provider);
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  const signIn = async (link, expected, { screenshot, currentAtActivation = false } = {}) => {
    const href = new URL(await link.getAttribute("href"), page.url());
    assert.equal(href.pathname, "/login");
    if (!currentAtActivation) assert.equal(href.searchParams.get("return_to"), expected);
    if (extension) {
      assert.equal(await link.getAttribute("target"), "_blank");
      assert.match(await link.getAttribute("rel"), /noopener/);
    }
    const popup = extension ? page.context().waitForEvent("page") : null;
    await link.click();
    const login = popup ? await popup : page;
    await login.waitForURL((url) => url.pathname === "/login");
    assert.equal(new URL(login.url()).searchParams.get("return_to"), expected);
    const proceed = login.getByRole("link", { name: "Continue to sign in", exact: true });
    await proceed.waitFor();
    if (screenshot) await login.screenshot({ path: screenshot, animations: "disabled" });
    const request = login.context().waitForEvent("request", {
      predicate: (request) => new URL(request.url()).pathname === "/api/v1/user/auth/login",
    });
    const response = login.context().waitForEvent("response", {
      predicate: (response) => new URL(response.url()).pathname === "/api/v1/user/auth/login",
    });
    await proceed.click();
    assert.equal(new URL((await request).url()).searchParams.get("return_to"), expected);
    const authorization = await response;
    assert.equal(authorization.status(), 302);
    const location = new URL(authorization.headers().location);
    assert.equal(location.origin + location.pathname, providerUrl.origin + providerUrl.pathname);
    await login.waitForURL(
      (url) => url.origin + url.pathname === providerUrl.origin + providerUrl.pathname,
    );
    await login.getByText("Sign-in provider", { exact: true }).waitFor();
    if (extension) await login.close();
  };
  try {
    // Every declared query rule crosses the actual login page and real Python auth route.
    if (!extension)
      for (const path of paths) {
        await page.goto(destination(`/login?${new URLSearchParams({ return_to: path })}`));
        const proceed = page.getByRole("link", { name: "Continue to sign in", exact: true });
        await proceed.waitFor();
        const response = page.waitForResponse(
          (response) => new URL(response.url()).pathname === "/api/v1/user/auth/login",
        );
        assert.equal(
          new URL(await proceed.getAttribute("href"), page.url()).searchParams.get("return_to"),
          path,
        );
        await proceed.click();
        assert.equal((await response).status(), 302, `${path}: approved callback destination`);
        await page.getByText("Sign-in provider", { exact: true }).waitFor();
      }

    // Following from a filtered native feed retains the collection, tab, and sort.
    const followFrom = async (path) => {
      await page.goto(destination(path));
      const follow = path.startsWith("/sources/")
        ? page.getByRole("link", { name: "Follow source", exact: true })
        : page
            .getByRole("region", { name: "Feed controls" })
            .getByRole("link", { name: "Follow", exact: true });
      await follow.waitFor();
      await signIn(follow, extension ? "/extension/login-complete" : path);
      if (extension)
        assert.equal(page.url(), destination(path), "sign-in keeps the new-tab reader");
    };
    await followFrom("/topics/javascript/tutorials?sort=most_liked");

    const consent = `/mcp/authorize?request=${"a".repeat(43)}`;
    await page.goto(destination(consent));
    await page
      .getByRole("heading", { name: "Sign in to connect your agent", exact: true })
      .waitFor();
    await signIn(
      page.locator(".empty-state").getByRole("link", { name: "Sign in", exact: true }),
      extension ? "/extension/login-complete" : consent,
    );

    // A saved/liked article returns to the article that the action applies to.
    const first = fixture.articles[0];
    await page.goto(destination(`/articles/${first.slug}`));
    const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
    await preview.getByRole("heading", { name: first.title, exact: true }).waitFor();
    await signIn(
      preview.getByRole("link", { name: "Sign in to save article for later", exact: true }),
      extension ? "/extension/login-complete" : `/articles/${first.slug}`,
      { screenshot: `${output}/article-sign-in.png` },
    );
    if (extension) {
      await preview.getByRole("heading", { name: first.title, exact: true }).waitFor();
      assert.equal(page.url(), destination(`/articles/${first.slug}`));
    }

    // The invitation captures the current filtered location when activated.
    await page.goto(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    await page.evaluate(() => sessionStorage.removeItem("devfeed:signup-nudge-articles"));
    for (const item of fixture.articles.slice(0, 3)) {
      await page.goto(destination(`/articles/${item.slug}`));
      await preview.getByRole("heading", { name: item.title, exact: true }).waitFor();
      await preview.getByRole("button", { name: "Close preview", exact: true }).click();
      await preview.waitFor({ state: "detached" });
    }
    await page.goto(destination("/latest"));
    const banner = page.getByRole("complementary", { name: "Create a DevFeed account" });
    await banner.waitFor();
    const filtered = "/latest?content_type=news&sort=most_liked";
    if (!extension)
      await page.evaluate((path) => history.replaceState(history.state, "", path), filtered);
    await page.screenshot({ path: `${output}/filtered-signup.png`, animations: "disabled" });
    await signIn(
      banner.getByRole("link", { name: "Create account", exact: true }),
      extension ? "/extension/login-complete" : filtered,
      { currentAtActivation: true },
    );
    await followFrom("/sources/publisher/news?sort=oldest");
    console.log("Shared login destinations retain native actions and approved filters.");
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error("Login destination failure", page.url());
    throw error;
  } finally {
    fixture.active = false;
  }
}
