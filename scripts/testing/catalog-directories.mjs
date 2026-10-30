import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { catalogChoices } from "./catalog-scroll.mjs";

/** Bounded API fixture shared by actual web, Chrome and Edge reader bundles. */
export function catalogDirectoriesFixture() {
  const catalogs = Object.fromEntries(
    ["topics", "sources", "tags"].map((kind) => {
      const items = catalogChoices(kind === "tags" ? "topics" : kind).slice(0, 61);
      return [
        kind,
        items.map((item, index) => ({
          ...item,
          slug: `directory-${kind}-${index}`,
          name: `${index < 60 ? "First" : "Last"} directory ${kind} ${index}`,
        })),
      ];
    }),
  );
  return {
    active: false,
    fail: false,
    catalogs,
    requests: [],
    handles(url) {
      return this.active && /^\/(?:api\/)?v1\/(topics|sources|tags)$/.test(url.pathname);
    },
    async respond(url) {
      this.requests.push(new URL(url));
      // A delayed empty response must still yield a real pre-stream document404.
      if (Number(url.searchParams.get("offset")) > 1000)
        await new Promise((resolve) => setTimeout(resolve, 150));
      if (this.fail) return { status: 503, json: { detail: "Temporary catalog failure" } };
      const kind = url.pathname.split("/").at(-1);
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      const letter = url.searchParams.get("letter");
      const matching = catalogs[kind].filter(
        (item) =>
          item.name.toLowerCase().includes(query) && (!letter || item.name.startsWith(letter)),
      );
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 61);
      const items = matching.slice(offset, offset + limit);
      return {
        status: 200,
        json: url.pathname.startsWith("/api/")
          ? {
              items: items.slice(0, 60),
              next_cursor: items.length > 60 ? String(offset + 60) : null,
            }
          : items,
      };
    },
  };
}

/** Direct URLs, bounded pagination, status/robots agreement and browser history. */
export async function checkCatalogDirectories(
  page,
  destination,
  fixture,
  output,
  extension = false,
) {
  await mkdir(output, { recursive: true });
  await page.addInitScript(() => {
    // Exercise the real links rather than letting automatic scrolling consume them.
    window.IntersectionObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    };
    if (location.protocol !== "about:") localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  await page.bringToFront();
  if (extension) await page.reload();
  fixture.active = true;
  const current = () => {
    const url = new URL(page.url());
    return extension ? new URL(url.hash.slice(1), "https://devfeed.tech") : url;
  };
  const cardLinks = (kind) =>
    page.locator(kind === "tags" ? ".tags-group:visible li a" : ".catalog-card:visible a:has(h2)");
  const heading = (kind) =>
    page.getByRole("heading", {
      name: kind === "sources" ? "Sources" : `Explore ${kind}`,
      exact: true,
    });
  const canonical = async (kind, offset = 0) => {
    if (extension) return;
    const url = new URL(destination(`/${kind}`));
    assert.equal(
      await page.locator('link[rel="canonical"]').getAttribute("href"),
      `${url.origin}/${kind}${offset ? `?offset=${offset}` : ""}`,
    );
  };
  try {
    for (const kind of ["topics", "sources", "tags"]) {
      fixture.requests.length = 0;
      const first = await page.goto(destination(`/${kind}?q=directory`));
      await heading(kind).waitFor();
      assert.equal(await cardLinks(kind).count(), 60);
      if (!extension) {
        assert.equal(first.status(), 200);
        assert.equal(fixture.requests.length, 1, "metadata and page share one catalog snapshot");
      }
      const next = page.locator('a[rel="next"]');
      assert.equal(
        await next.count(),
        1,
        JSON.stringify({
          requests: fixture.requests.map((url) => Object.fromEntries(url.searchParams)),
          links: await page.locator("a.button").allTextContents(),
          text: await page.locator(".pagination").allTextContents(),
        }),
      );
      assert.equal(
        await next.getAttribute("href"),
        extension ? `#/${kind}?q=directory&offset=60` : `/${kind}?q=directory&offset=60`,
      );
      await next.click();
      await page.waitForFunction(() => location.href.includes("offset=60"));
      await cardLinks(kind)
        .filter({ hasText: `Last directory ${kind} 60` })
        .waitFor();
      assert.equal(await cardLinks(kind).count(), 1);
      assert.equal(await page.locator('a[rel="next"]').count(), 0);
      await canonical(kind, 60);
      await page.goBack();
      await heading(kind).waitFor();
      await cardLinks(kind)
        .filter({ hasText: `First directory ${kind} 0` })
        .waitFor();
      assert.equal(current().searchParams.get("offset"), null);
      await page.goForward();
      await cardLinks(kind)
        .filter({ hasText: `Last directory ${kind} 60` })
        .waitFor();
      assert.equal(current().searchParams.get("offset"), "60");
      await page.getByRole("link", { name: `Previous ${kind}`, exact: true }).click();
      await cardLinks(kind)
        .filter({ hasText: `First directory ${kind} 0` })
        .waitFor();
      assert.equal(current().searchParams.get("q"), "directory");
      assert.equal(current().searchParams.get("offset"), null);
      await page.goto(destination(`/${kind}?q=First`));
      await heading(kind).waitFor();
      assert.equal(await cardLinks(kind).count(), 60);
      assert.equal(
        await page.locator('a[rel="next"]').count(),
        0,
        "an exact60-item ending has no ghost continuation",
      );
      await page.goto(destination(`/${kind}?offset=00060&q=%20directory%20`));
      await cardLinks(kind)
        .filter({ hasText: `Last directory ${kind} 60` })
        .waitFor();
      await canonical(kind, 60);
      const normalized = fixture.requests.at(-1);
      assert.equal(normalized.searchParams.get("offset"), "60");
      assert.equal(normalized.searchParams.get("q"), "directory");
      for (const suffix of ["?q=unmatched", ...(kind === "tags" ? ["?letter=Z"] : [])]) {
        const empty = await page.goto(destination(`/${kind}${suffix}`));
        await heading(kind).waitFor();
        await page.locator(".empty-state h2").waitFor();
        assert.equal(await cardLinks(kind).count(), 0);
        if (!extension) {
          assert.equal(empty.status(), 200);
          assert.match(
            await page.locator('meta[name="robots"]').getAttribute("content"),
            /noindex, follow/,
          );
        }
        await canonical(kind);
      }
      for (const offset of ["-1", "invalid", "1e3"]) {
        await page.goto(destination(`/${kind}?offset=${offset}`));
        await heading(kind).waitFor();
        assert.equal(await cardLinks(kind).count(), 60);
        await canonical(kind);
      }
      const missing = await page.goto(destination(`/${kind}?offset=999999`));
      await page.getByRole("heading", { name: "Page not found", exact: true }).waitFor();
      if (!extension) {
        assert.equal(missing.status(), 404);
        for (const userAgent of ["Mozilla/5.0", "Googlebot"]) {
          const response = await page.request.get(destination(`/${kind}?offset=999999`), {
            headers: { "User-Agent": userAgent },
          });
          assert.equal(response.status(), 404, `${userAgent} gets pre-stream404`);
          assert.match(await response.text(), /<meta name="robots" content="noindex/);
        }
      }
      await page.screenshot({ path: `${output}/${kind}-missing.png`, fullPage: true });
      fixture.fail = true;
      const failed = await page.goto(destination(`/${kind}`));
      await page
        .getByRole("heading", {
          name: extension ? `Couldn’t load ${kind}` : "This page couldn’t load",
          exact: true,
        })
        .waitFor();
      if (!extension) assert.equal(failed.status(), 200);
      fixture.fail = false;
      await page.getByRole("button", { name: "Try again", exact: true }).click();
      await heading(kind).waitFor();
      await cardLinks(kind).first().waitFor();
    }
    console.log(
      `Catalog directory existence, bounded filters and history passed (${extension ? "extension" : "web"}).`,
    );
  } finally {
    fixture.active = false;
    fixture.fail = false;
  }
}
