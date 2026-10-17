import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");
export function youtubeReaderFixture(article) {
  const channel = {
    ...article.sources[0],
    id: "33333333-3333-4333-8333-333333333331",
    slug: "engineering-channel",
    name: "Engineering videos",
    provider: "youtube",
    resource_kind: "channel",
    provider_resource_id: "UC0123456789012345678901",
    recurring: true,
    website_url: "https://www.youtube.com/channel/UC0123456789012345678901",
    logo_url: null,
    description: "Technical videos about software engineering.",
  };
  const playlist = {
    ...channel,
    id: "33333333-3333-4333-8333-333333333332",
    slug: "concurrency-playlist",
    name: "Concurrency playlist",
    resource_kind: "playlist",
    provider_resource_id: "PLDeveloperVideos",
    website_url: "https://www.youtube.com/playlist?list=PLDeveloperVideos",
  };
  const direct = {
    ...channel,
    id: "33333333-3333-4333-8333-333333333333",
    slug: "single-developer-video",
    name: "Single submitted video",
    resource_kind: "video",
    provider_resource_id: "dQw4w9WgXcQ",
    recurring: false,
    website_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  };
  const video = {
    ...article,
    id: "22222222-2222-4222-8222-222222222220",
    slug: "developer-video",
    title: "Thread safety in developer tools",
    canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    content_format: "video",
    content_type: "tutorial",
    provider: "youtube",
    provider_resource_id: "dQw4w9WgXcQ",
    image_url: "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    image_variants: [],
    ai_summary:
      "A technical discussion of thread safety, synchronization, and testing developer tools.",
    summary: "This engineering talk explains synchronization and how to test concurrent software.",
    published_at: "2024-04-15T10:00:00Z",
    feed_at: "2026-10-17T00:00:00Z",
    sources: [channel, playlist],
    video_metadata: {
      channel_id: channel.provider_resource_id,
      channel_title: channel.name,
      channel_url: channel.website_url,
      duration_seconds: 3723,
      availability: "public",
      retrieved_at: "2026-10-17T00:00:00Z",
    },
  };
  const state = {
    active: false,
    liked: false,
    bookmarked: false,
    opens: 0,
    sourceIds: [],
    view: "cards",
    mutations: [],
    requests: [],
  };
  const engagement = () => ({
    article_id: video.id,
    likes: state.liked ? 1 : 0,
    liked: state.liked,
    opens: state.opens,
    bookmarked: state.bookmarked,
  });
  const paths = new Set([
    "/v1/feed",
    "/v1/feed/options",
    "/v1/user/auth/me",
    "/v1/user/settings/profile",
    "/v1/user/settings/feed",
    "/v1/user/preferences/sources",
    "/v1/user/engagement",
    "/v1/user/bookmarks",
    "/v1/search",
  ]);
  return {
    state,
    video,
    channel,
    playlist,
    direct,
    handles(url) {
      const path = apiPath(url);
      return (
        state.active &&
        (paths.has(path) ||
          path === `/v1/articles/${video.slug}` ||
          [channel, playlist, direct].some((source) => path === `/v1/sources/${source.slug}`) ||
          path.startsWith(`/v1/user/articles/${video.id}/`) ||
          path.startsWith("/v1/user/preferences/sources/"))
      );
    },
    respond(url, method = "GET", payload) {
      const path = apiPath(url);
      state.requests.push({ path, method });
      let json;
      if (path === "/v1/user/auth/me")
        json = {
          user_id: "44444444-4444-4444-8444-444444444444",
          name: "Video reader",
          csrf_token: "test",
          expires_at: 4102444800,
        };
      else if (path === "/v1/user/settings/profile")
        json = {
          display_name: "Video reader",
          avatar_url: null,
          reading_streak: { current_days: 0, longest_days: 0, total_days: 0, last_read_date: null },
        };
      else if (path === "/v1/user/settings/feed") {
        if (method === "PUT") state.view = payload.view;
        json = {
          view: state.view,
          languages: ["en"],
          content_types: ["tutorial", "article", "news", "release", "comparison", "opinion"],
        };
      } else if (path === "/v1/feed" || path === "/v1/user/bookmarks")
        json = {
          items: path.endsWith("bookmarks") && !state.bookmarked ? [] : [video],
          next_cursor: null,
        };
      else if (path === "/v1/feed/options")
        json = { sources: [channel, playlist], languages: ["en"], content_types: ["tutorial"] };
      else if (path === `/v1/articles/${video.slug}`)
        json = url.pathname.startsWith("/api/") ? { article: video, topic: null } : video;
      else if (path === `/v1/sources/${channel.slug}`) json = channel;
      else if (path === `/v1/sources/${playlist.slug}`) json = playlist;
      else if (path === `/v1/sources/${direct.slug}`) json = direct;
      else if (path === "/v1/user/engagement") json = [engagement()];
      else if (path === "/v1/user/preferences/sources") json = { source_ids: state.sourceIds };
      else if (path.startsWith("/v1/user/preferences/sources/")) {
        const id = path.split("/").at(-1);
        state.sourceIds = payload.followed
          ? [...new Set([...state.sourceIds, id])]
          : state.sourceIds.filter((value) => value !== id);
        state.mutations.push({ path, payload });
        json = { followed: payload.followed };
      } else if (path.startsWith(`/v1/user/articles/${video.id}/`)) {
        if (path.endsWith("/like")) state.liked = payload.liked;
        if (path.endsWith("/bookmark")) state.bookmarked = payload.bookmarked;
        if (path.endsWith("/open")) state.opens++;
        state.mutations.push({ path, payload });
        json = path.endsWith("/bookmark")
          ? { article_id: video.id, bookmarked: state.bookmarked }
          : engagement();
      } else if (path === "/v1/search")
        json = {
          query: url.searchParams.get("q"),
          sections: {
            articles: {
              items: [
                {
                  id: video.id,
                  title: video.title,
                  description: video.ai_summary,
                  href: `/articles/${video.slug}`,
                  image_url: video.image_url,
                  published_at: video.published_at,
                  label: video.content_type,
                  content_format: "video",
                  provider: "youtube",
                  video_metadata: video.video_metadata,
                },
              ],
              next_cursor: null,
            },
          },
        };
      return { json };
    },
  };
}

/** Exercise shared production reader components in the website and both unpacked extensions. */
export async function checkYoutubeReader(page, destination, fixture, output) {
  await mkdir(output, { recursive: true });
  fixture.state.active = true;
  await page.context().route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270"><rect width="480" height="270" fill="#12384a"/><text x="30" y="145" fill="white" font-size="28">Engineering talk</text></svg>',
    }),
  );
  await page
    .context()
    .route("https://www.googletagmanager.com/**", (route) =>
      route.fulfill({ contentType: "text/javascript", body: "window.gtag = () => {};" }),
    );
  await page
    .context()
    .route("https://www.google-analytics.com/**", (route) => route.fulfill({ status: 204 }));
  await page
    .context()
    .route(fixture.video.canonical_url, (route) =>
      route.fulfill({ contentType: "text/html", body: "<h1>YouTube watch page</h1>" }),
    );
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("about:blank");
  await page.goto(destination("/latest"));
  const card = page.locator(".article-card").filter({ hasText: fixture.video.title });
  await card.waitFor();
  try {
    await card.getByRole("button", { name: "Like video, 0 likes", exact: true }).waitFor();
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    throw error;
  }
  const watch = card.getByRole("link", { name: "Watch on YouTube in a new tab", exact: true });
  assert.equal(await watch.getAttribute("href"), fixture.video.canonical_url);
  assert.equal(await card.getByText("Video", { exact: true }).count(), 1);
  assert.equal(await card.getByText("1:02:03", { exact: true }).count(), 1);
  await page.waitForFunction(() => {
    const image = document.querySelector(".article-card .card-image img");
    return image?.complete && image.naturalWidth > 0;
  });
  assert.equal(
    await card.locator(".card-date").getAttribute("datetime"),
    fixture.video.published_at,
  );
  await card.getByRole("button", { name: "Like video, 0 likes", exact: true }).click();
  await card.getByRole("button", { name: "Unlike video, 1 likes", exact: true }).waitFor();
  await card.getByRole("button", { name: "Save video for later", exact: true }).click();
  await card.getByRole("button", { name: "Remove bookmark", exact: true }).waitFor();
  const popupReady = page.context().waitForEvent("page");
  await watch.click();
  const popup = await popupReady;
  await popup.getByRole("heading", { name: "YouTube watch page", exact: true }).waitFor();
  await popup.close();
  for (let attempt = 0; attempt < 100 && fixture.state.opens < 1; attempt++)
    await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(
    fixture.state.opens,
    1,
    "watching a link records the existing original-open metric once",
  );
  await page.screenshot({ path: `${output}/video-card.png`, animations: "disabled" });
  await page.goto(destination("/read-later"));
  await page.getByRole("link", { name: fixture.video.title, exact: true }).waitFor();
  await page.getByRole("link", { name: fixture.video.title, exact: true }).click();
  await page.locator("#article-preview-title").waitFor();
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  assert.equal(
    await preview
      .getByRole("link", { name: "Watch on YouTube in a new tab", exact: true })
      .getAttribute("href"),
    fixture.video.canonical_url,
  );
  await preview
    .getByText("Opening a link does not confirm playback or completion.", { exact: false })
    .waitFor();
  assert.equal(await preview.locator(".source-brief").count(), 2);
  await preview
    .getByRole("button", { name: `Share video: ${fixture.video.title}`, exact: true })
    .click();
  const share = page.getByRole("dialog", { name: "Share video", exact: true });
  await share.waitFor();
  const reddit = new URL(
    await share
      .getByRole("link", { name: "Share on Reddit (opens in a new tab)", exact: true })
      .getAttribute("href"),
  );
  assert.equal(new URL(reddit.searchParams.get("url")).pathname, `/articles/${fixture.video.slug}`);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: `${output}/video-preview-mobile.png`, animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await preview.getByRole("button", { name: "Close preview", exact: true }).click();
  await page.goto(destination(`/sources/${fixture.playlist.slug}`));
  await page.getByRole("heading", { name: fixture.playlist.name, exact: true }).waitFor();
  await page.getByText("YouTube playlist · Recurring collection", { exact: true }).waitFor();
  await page
    .getByRole("region", { name: "Feed controls" })
    .getByRole("button", { name: "Follow source", exact: true })
    .click();
  await page
    .getByRole("region", { name: "Feed controls" })
    .getByRole("button", { name: "Following source", exact: true })
    .waitFor();
  assert.deepEqual(fixture.state.sourceIds, [fixture.playlist.id]);
  await page.getByRole("button", { name: "List view", exact: true }).click();
  const row = page.locator(".article-list-row").filter({ hasText: fixture.video.title });
  await row.waitFor();
  assert.equal(await row.getByText("Video", { exact: true }).count(), 1);
  assert.equal(await row.getByText("1:02:03", { exact: true }).count(), 1);
  await page.screenshot({ path: `${output}/video-playlist-compact.png`, animations: "disabled" });
  await page.goto(destination("/search?q=video-engineering"));
  const hit = page.locator(".search-result").filter({ hasText: fixture.video.title });
  await hit.waitFor();
  assert.equal(await hit.getByText("Video", { exact: true }).count(), 1);
  assert.equal(await hit.getByText("1:02:03", { exact: true }).count(), 1);
  await page.goto(destination(`/sources/${fixture.direct.slug}`));
  await page.getByRole("heading", { name: fixture.direct.name, exact: true }).waitFor();
  await page.getByText("YouTube video · Single submission", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("region", { name: "Feed controls" })
      .getByRole("button", { name: /Follow source|Following source/ })
      .count(),
    0,
  );
  assert.ok(
    fixture.state.mutations.some((item) => item.path.endsWith(`/${fixture.video.id}/like`)),
  );
  assert.ok(
    fixture.state.mutations.some((item) => item.path.endsWith(`/${fixture.video.id}/bookmark`)),
  );
}
