import { tagFixtures } from "./tags.mjs";
import assert from "node:assert/strict";
import { chooseArticleView, openArticleViewSettings } from "./article-view.mjs";
import { createFixtureFeedViews } from "./feed-view-fixture.mjs";

/** One backend fixture drives the real website and both unpacked extension readers. */
export function userMutationFixture({ articles, topic }) {
  let engagementReads = 0;
  let liked = false;
  let bookmarked = false;
  let followed = false;
  const tagIds = new Set();
  let feed = {
    view: "cards",
    content_types: ["article", "news", "tutorial", "release", "comparison", "opinion"],
    languages: ["en"],
  };
  const scopedViews = createFixtureFeedViews(() => feed.view);
  const failures = new Set(["like", "follow", "layout", "feed", "tag-follow"]);
  const writes = [];
  const response = (json, status = 200) => ({ status, json });
  const attempt = (operation, payload, apply) => {
    writes.push({ operation, payload });
    if (failures.delete(operation)) return response({ detail: "Temporary fixture failure" }, 503);
    return response(apply());
  };
  const operations = new Map([
    [
      `GET /v1/topics/${encodeURIComponent(topic.slug)}`,
      () =>
        response({
          ...topic,
          logo_url: null,
          description: "Mutation fixture topic",
          ai_description: null,
        }),
    ],
    [
      "GET /v1/user/auth/me",
      () =>
        response({
          user_id: "mutation-reader",
          name: "Private reader name",
          email: "private-reader@example.test",
          csrf_token: "test",
          expires_at: Date.now() / 1000 + 3600,
        }),
    ],
    [
      "GET /v1/user/settings/profile",
      () => response({ display_name: "Private reader name", avatar_url: null }),
    ],
    ["GET /v1/user/settings/feed-view", (_, scope) => response(scopedViews.read(scope))],
    [
      "PUT /v1/user/settings/feed-view",
      (payload, scope) => attempt("layout", payload, () => scopedViews.write(scope, payload)),
    ],
    ["GET /v1/user/settings/feed", () => response(feed)],
    [
      "PUT /v1/user/settings/feed",
      (payload) => attempt("feed", payload, () => (feed = { ...feed, ...payload })),
    ],
    [
      "GET /v1/user/settings/appearance",
      () =>
        response({
          theme: "system",
          timezone: "local",
          date_format: "locale",
          time_format: "system",
        }),
    ],
    ["GET /v1/user/preferences", () => response({ topic_ids: followed ? [topic.id] : [] })],
    ["GET /v1/user/preferences/sources", () => response({ source_ids: [] })],
    ["GET /v1/user/preferences/tags", () => response({ tag_ids: [...tagIds] })],
    ...tagFixtures.map((tag) => [
      `PUT /v1/user/preferences/tags/${tag.id}`,
      (payload) =>
        attempt("tag-follow", payload, () => {
          if (payload.followed) tagIds.add(tag.id);
          else tagIds.delete(tag.id);
          return { followed: payload.followed };
        }),
    ]),
    [
      `PUT /v1/user/preferences/topics/${encodeURIComponent(topic.id)}`,
      (payload) =>
        attempt("follow", payload, () => {
          followed = payload.followed;
          return { followed };
        }),
    ],
    [
      `PUT /v1/user/articles/${encodeURIComponent(articles[0].id)}/like`,
      (payload) =>
        attempt("like", payload, () => {
          liked = payload.liked;
          return { article_id: articles[0].id, liked, likes: liked ? 1 : 0, opens: 5, bookmarked };
        }),
    ],
    [
      `PUT /v1/user/articles/${encodeURIComponent(articles[0].id)}/bookmark`,
      (payload) =>
        attempt("bookmark", payload, () => {
          bookmarked = payload.bookmarked;
          return { article_id: articles[0].id, bookmarked };
        }),
    ],
    [
      "GET /v1/user/engagement",
      () => {
        if (++engagementReads <= 2)
          return response({ detail: "Temporary engagement failure" }, 503);
        return response(
          articles.map((article) => ({
            article_id: article.id,
            liked,
            likes: liked ? 1 : 0,
            opens: 5,
            bookmarked,
          })),
        );
      },
    ],
  ]);
  return {
    active: false,
    articles,
    topic,
    writes,
    scopedViews,
    get engagementReads() {
      return engagementReads;
    },
    handles(path, method) {
      return this.active && operations.has(`${method} ${path}`);
    },
    respond(path, method, payload, scope) {
      return operations.get(`${method} ${path}`)(payload, scope);
    },
  };
}

export async function checkUserMutations(page, destination, fixture) {
  const originalViewport = page.viewportSize();
  fixture.active = true;
  await page.addInitScript(() => {
    window.__mutationEvents = [];
    window.__mutationInterests = 0;
    window.addEventListener("devfeed:analytics", (event) => {
      window.__mutationEvents.push(event.detail);
    });
    window.addEventListener("devfeed:interests-changed", () => {
      window.__mutationInterests += 1;
    });
  });
  const clearEffects = () =>
    page.evaluate(() => {
      window.__mutationEvents = [];
      window.__mutationInterests = 0;
    });
  const effects = () =>
    page.evaluate(() => ({
      events: window.__mutationEvents,
      interests: window.__mutationInterests,
    }));
  try {
    await page.setViewportSize({ width: 520, height: 1000 });
    await page.goto(destination("/latest"));
    await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    const card = page.locator(".article-card").first();
    await card.waitFor();
    await clearEffects();
    const like = card.getByRole("button", { name: /^Like article/ });
    await like.click();
    assert.ok(
      fixture.engagementReads >= 3,
      "Signed-in actions recover after failed engagement reads without navigation",
    );
    await card.getByText("Couldn’t save. Try again.", { exact: true }).waitFor();
    assert.deepEqual(
      await effects(),
      { events: [], interests: 0 },
      "Rejected likes publish no success or feed invalidation",
    );
    await like.click();
    const unlike = card.getByRole("button", { name: /^Unlike article/ });
    await unlike.waitFor();
    await unlike.click();
    await like.waitFor();
    await card.getByRole("button", { name: "Save article for later", exact: true }).click();
    await card.getByRole("button", { name: "Remove bookmark", exact: true }).click();
    await card.getByRole("button", { name: "Save article for later", exact: true }).waitFor();
    assert.deepEqual(
      await effects(),
      {
        events: [
          { name: "article_like", params: { article_id: fixture.articles[0].id } },
          { name: "article_unlike", params: { article_id: fixture.articles[0].id } },
          { name: "article_bookmark", params: { article_id: fixture.articles[0].id } },
          { name: "article_unbookmark", params: { article_id: fixture.articles[0].id } },
        ],
        interests: 0,
      },
      "Article mutations emit caller identities once without replacing the personal feed",
    );

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(destination(`/topics/${fixture.topic.slug}`));
    const controls = page.getByRole("region", { name: "Feed controls" });
    const follow = controls.getByRole("button", { name: "Follow", exact: true });
    await follow.waitFor();
    await clearEffects();
    await follow.click();
    await controls.getByRole("alert").waitFor();
    assert.deepEqual(
      await effects(),
      { events: [], interests: 0 },
      "Rejected follows do not invalidate interests",
    );
    await follow.click();
    await controls.getByRole("button", { name: "Following", exact: true }).waitFor();
    assert.deepEqual(await effects(), {
      events: [{ name: "topic_follow", params: { topic_id: fixture.topic.id } }],
      interests: 1,
    });

    const layout = await openArticleViewSettings(page);
    await clearEffects();
    // The deliberately rejected save can roll back before Playwright's check()
    // verifies the optimistic state. Observe the actual error and saved choice.
    await layout.getByRole("radio", { name: "List", exact: true }).click();
    await layout.getByRole("alert").waitFor();
    assert.equal(await layout.getByRole("radio", { name: "Grid", exact: true }).isChecked(), true);
    assert.deepEqual(await effects(), { events: [], interests: 0 });
    await chooseArticleView(page, "compact");
    assert.deepEqual(
      await effects(),
      { events: [], interests: 0 },
      "Changing only a layout does not invalidate article recommendations",
    );
    await page.goto(destination("/settings/feed"));
    await page.getByRole("checkbox", { name: "Tutorials", exact: true }).waitFor();
    assert.equal(await page.getByRole("radio").count(), 0);
    await clearEffects();
    await page.getByRole("checkbox", { name: "Tutorials", exact: true }).uncheck();
    const save = page.getByRole("button", { name: "Save changes", exact: true });
    await save.click();
    await page
      .getByText("Couldn’t save your feed settings. Please try again.", { exact: true })
      .waitFor();
    assert.deepEqual(
      await effects(),
      { events: [], interests: 0 },
      "Rejected settings do not publish success or invalidate the feed",
    );
    await save.click();
    await page.getByText("Feed settings saved.", { exact: true }).waitFor();
    assert.deepEqual(await effects(), {
      events: [{ name: "feed_settings_saved", params: { feed_view: "cards", selected_count: 5 } }],
      interests: 1,
    });
    await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
    await save.click();
    await page.getByText("Feed settings saved.", { exact: true }).waitFor();
    assert.equal(
      fixture.writes.at(-1).payload.view,
      "cards",
      "Feed reset preserves legacy defaults",
    );
    assert.equal(
      fixture.scopedViews.read("my_feed").view,
      "compact",
      "Feed reset preserves scoped layouts",
    );
    const serialized = JSON.stringify(await effects());
    assert.ok(
      !serialized.includes("Private reader name") &&
        !serialized.includes("private-reader@example.test"),
    );
    assert.deepEqual(
      fixture.writes.map(({ operation }) => operation),
      [
        "like",
        "like",
        "like",
        "bookmark",
        "bookmark",
        "follow",
        "follow",
        "layout",
        "layout",
        "feed",
        "feed",
        "feed",
      ],
    );
  } finally {
    fixture.active = false;
    await page.setViewportSize(originalViewport);
  }
}
