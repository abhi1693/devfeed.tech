import assert from "node:assert/strict";

/** Inspect page views emitted by the installed extension, without a live collector. */
export async function checkExtensionRouteAnalytics(page, destination, events) {
  const query = "private-query-that-must-not-be-telemetry";
  const request = "private-authorization-request";
  const routes = [
    { path: `/trending?q=${query}`, label: "/trending", heading: "Trending" },
    { path: `/leaderboard?account=${query}`, label: "/leaderboard", heading: "Leaderboard" },
    {
      path: `/dev-card?username=${query}`,
      label: "/dev-card",
      heading: "A dev card that’s yours.",
    },
    { path: `/mcp?client=${query}`, label: "/mcp", heading: "Connect your agent" },
    {
      path: `/mcp/authorize?request=${request}`,
      label: "/mcp/authorize",
      heading: "Sign in to connect your agent",
    },
    { path: `/users/reader?q=${query}`, label: "/users/profile", heading: "Reader Profile" },
    {
      path: `/articles/direct-article?tracking=${query}`,
      label: "/articles",
      heading: "Shared reader components in every new tab",
    },
    {
      path: `/topics/javascript/news?q=${query}`,
      label: "/topics/detail",
    },
    {
      path: `/sources/publisher/tutorials?q=${query}`,
      label: "/sources/detail",
    },
    {
      path: `/topics/javascript/unknown-format?q=${query}`,
      label: "/other",
      heading: "Page not found",
    },
    { path: `/not-a-reader-page?secret=${query}`, label: "/other", heading: "Page not found" },
  ];
  await page.bringToFront();
  // The fixture's homepage envelope may still be waiting for its analytics identity lock.
  let initialView;
  for (let attempt = 0; attempt < 150 && !initialView; attempt++) {
    initialView = events.find((value) => value.event.name === "page_view");
    if (!initialView) await page.waitForTimeout(20);
  }
  assert.deepEqual(initialView?.event.params, { page_path: "/" }, "initial homepage view");
  for (const { path, label, heading } of routes) {
    const before = events.length;
    await page.goto(destination(path));
    if (heading) await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    let pageView;
    for (let attempt = 0; attempt < 150 && !pageView; attempt++) {
      pageView = events.slice(before).find((value) => value.event.name === "page_view");
      if (!pageView) await page.waitForTimeout(20);
    }
    assert.ok(pageView, `${path}: a built extension emitted its real page_view`);
    assert.deepEqual(pageView.event.params, { page_path: label }, `${path}: approved label`);
  }
  const sent = JSON.stringify(events);
  for (const privateValue of [
    query,
    request,
    "direct-article",
    "javascript",
    "publisher",
    "reader",
  ])
    assert.equal(
      sent.includes(privateValue),
      false,
      `${privateValue} is absent from all envelopes`,
    );
}
