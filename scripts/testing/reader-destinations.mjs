import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { feedModeFixture } from "./feed-mode.mjs";

const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");
const inboxPath = "/v1/user/notifications/chimely/v1/inbox/";

/** Shared public article, authenticated inbox, and navigation evidence for all readers. */
export function readerDestinationsFixture(article) {
  const feed = feedModeFixture(article);
  const retryArticle = {
    ...feed.articles[0],
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    slug: "navigation-retry",
    title: "Recovered navigation article",
  };
  return {
    active: false,
    articles: feed.articles,
    retryArticle,
    failedArticle: true,
    inboxReads: [],
    handles(url) {
      feed.active = this.active;
      return (
        this.active &&
        (apiPath(url).startsWith(inboxPath) ||
          [
            `/v1/articles/${retryArticle.slug}`,
            "/v1/user/notifications/config",
            "/v1/user/settings/notifications",
          ].includes(apiPath(url)) ||
          feed.handles(url))
      );
    },
    async respond(url, payload) {
      const path = apiPath(url);
      feed.signedIn = true;
      if (path === `/v1/articles/${retryArticle.slug}`)
        return this.failedArticle
          ? { status: 503, json: { detail: "Article unavailable" } }
          : {
              status: 200,
              json: url.pathname.startsWith("/api/")
                ? { article: retryArticle, topic: feed.topic }
                : retryArticle,
            };
      if (path === "/v1/user/notifications/config")
        return {
          status: 200,
          json: { enabled: true, environment: "users", subscriber_id: "user_a" },
        };
      if (path === "/v1/user/settings/notifications")
        return { status: 200, json: { show_badge: true, sound: false } };
      if (path.startsWith(inboxPath)) {
        const operation = path.slice(inboxPath.length);
        if (operation === "stream")
          return { status: 200, contentType: "text/event-stream", body: ": connected\n\n" };
        if (operation === "items")
          return {
            status: 200,
            json: {
              items: [
                {
                  id: "notif_" + "a".repeat(26),
                  source: "notification",
                  category: "topic.article",
                  payload: {
                    title: "Open recommended navigation article",
                    body: "A relevant article from your followed topic.",
                    action_url: `/articles/${feed.articles[7].slug}`,
                  },
                  occurred_at: "2026-10-14T10:00:00Z",
                  read: false,
                  archived: false,
                },
              ],
              next_cursor: null,
            },
          };
        if (operation === "counts") return { status: 200, json: { unread: 1, unseen: 1 } };
        if (operation === "preferences") return { status: 200, json: { preferences: [] } };
        if (operation.endsWith("/read")) this.inboxReads.push(operation);
        return { status: 200, json: {} };
      }
      return feed.respond(url, payload);
    },
  };
}

/** Assert user-visible destinations and retained DOM/scroll rather than serialized URL policy. */
export async function checkReaderDestinations(
  page,
  destination,
  fixture,
  output,
  extension = false,
) {
  await mkdir(output, { recursive: true });
  fixture.active = true;
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  const scroll = () => page.evaluate(() => scrollY);
  const waitAtScroll = (top) =>
    page.waitForFunction((value) => Math.abs(scrollY - value) <= 2, top);
  const singlePreview = async (title) => {
    await preview.getByRole("heading", { name: title, exact: true }).waitFor();
    assert.equal(await page.locator("dialog.article-modal[open]").count(), 1);
  };
  const close = async () => {
    await preview.getByRole("button", { name: "Close preview", exact: true }).click();
    await preview.waitFor({ state: "detached" });
  };
  const readyFeed = async () => {
    // The web's server cards precede session/preferences hydration; interact with
    // the settled reader after its content-type controls and full list are ready.
    await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    await page.locator(".feed-toolbar").getByRole("link", { name: "News", exact: true }).waitFor();
    await page
      .locator(".article-card")
      .nth(fixture.articles.length - 1)
      .waitFor();
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
  };
  const inboxStylesApplied = async () => {
    if (extension) return;
    await page.waitForFunction(() => {
      const style = document.head.querySelector("style[data-chimely]");
      return style instanceof HTMLStyleElement && !!style.nonce && !!style.sheet?.cssRules.length;
    });
    assert.equal(
      await page.locator("style[data-chimely]").count(),
      1,
      "the server installs the nonce-authorized inbox stylesheet once",
    );
  };
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  if (!extension)
    await page
      .context()
      .addCookies([{ name: "devfeed_user_session", value: "valid", url: destination("/") }]);
  try {
    await page.goto(destination("/latest"));
    await readyFeed();
    await inboxStylesApplied();
    const opened = 9;
    await page.locator(".card-open-link").nth(opened).scrollIntoViewIfNeeded();
    const originalScroll = await scroll();
    assert.ok(originalScroll > 200, "the regression starts from a scrolled feed");
    const retained = await page.locator(".article-card").first().elementHandle();
    await page.locator(".card-open-link").nth(opened).click();
    await singlePreview(fixture.articles[opened].title);
    await preview.getByRole("button", { name: "Next article", exact: true }).click();
    await singlePreview(fixture.articles[opened + 1].title);
    assert.equal(await retained.evaluate((node) => node.isConnected), true);
    await waitAtScroll(originalScroll);
    await page.screenshot({ path: `${output}/scrolled-preview.png`, animations: "disabled" });
    await close();
    await page.waitForURL(destination("/latest"));
    await waitAtScroll(originalScroll);
    assert.equal(await retained.evaluate((node) => node.isConnected), true);
    await page.goForward();
    await singlePreview(fixture.articles[opened + 1].title);
    await waitAtScroll(originalScroll);
    await page.goBack();
    await preview.waitFor({ state: "detached" });
    await waitAtScroll(originalScroll);
    await retained.dispose();

    // A direct entry owns one modal even when its retained original route is revisited.
    await page.goto("about:blank");
    await page.goto(destination(`/articles/${fixture.articles[0].slug}`));
    await singlePreview(fixture.articles[0].title);
    await page.locator(".article-card").first().waitFor({ state: "attached" });
    await page.waitForFunction(
      () => !document.querySelector('button[aria-label="Next article"]')?.disabled,
    );
    await preview.getByRole("button", { name: "Next article", exact: true }).click();
    await singlePreview(fixture.articles[1].title);
    await preview.getByRole("button", { name: "Previous article", exact: true }).click();
    await singlePreview(fixture.articles[0].title);
    await page.screenshot({ path: `${output}/direct-preview.png`, animations: "disabled" });
    await close();
    await page.locator(".article-card").first().waitFor();

    // An uncached failure remains closable; retry upgrades the same requested article.
    await page.goto("about:blank");
    await page.goto(destination(`/articles/${fixture.retryArticle.slug}`));
    await singlePreview("Couldn’t load the article");
    assert.equal(
      await preview.getByRole("button", { name: "Previous article" }).isEnabled(),
      false,
    );
    fixture.failedArticle = false;
    await preview.getByRole("button", { name: "Try again", exact: true }).click();
    await singlePreview(fixture.retryArticle.title);
    await close();
    await page.locator(".article-card").first().waitFor();

    // Article notifications retain both ordinary feeds and contextual backgrounds.
    for (const path of ["/latest", "/topics/javascript"]) {
      await page.goto("about:blank");
      await page.goto(destination(path));
      await readyFeed();
      await page.locator(".card-open-link").nth(opened).scrollIntoViewIfNeeded();
      const top = await scroll();
      const card = await page.locator(".article-card").first().elementHandle();
      await page.getByRole("button", { name: /^Notifications/ }).click();
      await inboxStylesApplied();
      await page.getByText("Open recommended navigation article", { exact: true }).waitFor();
      if (path === "/latest")
        await page.screenshot({ path: `${output}/inbox.png`, animations: "disabled" });
      await page.getByText("Open recommended navigation article", { exact: true }).click();
      await singlePreview(fixture.articles[7].title);
      assert.equal(await card.evaluate((node) => node.isConnected), true, `${path}: retained DOM`);
      await waitAtScroll(top);
      if (path === "/topics/javascript")
        await page.screenshot({
          path: `${output}/notification-preview.png`,
          animations: "disabled",
        });
      await close();
      await page.waitForURL(destination(path));
      await waitAtScroll(top);
      await card.dispose();
    }
    assert.equal(fixture.inboxReads.length, 2, "both notifications mark their item read");
    await page.getByRole("button", { name: /^Notifications/ }).click();
    const settings = page.getByRole("link", { name: "Notification settings", exact: true });
    assert.equal(
      await settings.getAttribute("href"),
      extension ? "#/settings/notifications" : "/settings/notifications",
      "the footer also supplies a valid native destination",
    );
    await settings.click();
    await page.waitForURL(destination("/settings/notifications"));
    await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
    await page.getByRole("heading", { name: "Inbox events", exact: true }).waitFor();
    await waitAtScroll(0);
    assert.equal(await page.locator("dialog.article-modal[open]").count(), 0);
    await page.screenshot({ path: `${output}/notification-settings.png`, animations: "disabled" });
    console.log(
      "Typed destinations, one article overlay, feed/history scroll, and inbox navigation passed.",
    );
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error("Reader destination failure", page.url());
    throw error;
  } finally {
    fixture.active = false;
  }
}
