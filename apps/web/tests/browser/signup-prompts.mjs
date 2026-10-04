import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const slugs = ["signup-prompt-one", "signup-prompt-two", "signup-prompt-three"];
const invitationKey = "devfeed:dev-card-promo-dismissed";
export const promptFeaturedPath = /\/api\/v1\/users\/asaharan(?:\?.*)?$/;
export const promptFeatured = (route) =>
  route.fulfill({
    json: {
      profile: {
        username: "asaharan",
        display_name: "Featured Reader",
        avatar_url: null,
        stack: [],
      },
    },
  });

export function signupPromptArticle(article, slug) {
  const index = slugs.indexOf(slug);
  return index < 0
    ? null
    : {
        ...article,
        id: article.id.slice(0, -1) + (index + 1),
        slug,
        title: `Signup prompt article ${index + 1}`,
      };
}

// One browser journey runs against the website and the actual Chrome/Edge packages.
export async function checkSignupPrompts(context, home, output, { extension = false } = {}) {
  await mkdir(output, { recursive: true });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const href = (path) => (extension ? `${home.split("#")[0]}#${path}` : new URL(path, home).href);
  const banner = page.getByRole("complementary", { name: "Create a DevFeed account" });
  const preview = page.getByRole("dialog", { name: "Your dev card preview" });
  await page.route(promptFeaturedPath, promptFeatured);
  await page.clock.install();
  await page.emulateMedia({ reducedMotion: "reduce" });
  try {
    for (const [index, slug] of slugs.entries()) {
      await page.goto(href(`/articles/${slug}`));
      const article = page.getByRole("dialog", { name: "Article preview" });
      await article.getByRole("heading", { name: `Signup prompt article ${index + 1}` }).waitFor();
      assert.equal(await banner.count(), 0, "reading an article takes priority over signup");
      await article.getByRole("button", { name: "Close preview" }).click();
      await article.waitFor({ state: "detached" });
      if (index < 2) assert.equal(await banner.count(), 0, "wait for three distinct articles");
    }
    await banner.waitFor();
    await page.clock.fastForward(35_000);
    assert.equal(await preview.isVisible(), false, "the banner holds back automatic Dev Cards");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.clock.runFor(100);
    await page.locator(".article-card").first().waitFor();
    await banner.waitFor();
    await page.screenshot({ path: `${output}/signup-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    const dismiss = banner.getByRole("button", { name: "Not now" });
    const bounds = await dismiss.boundingBox();
    assert.ok(bounds && bounds.height >= 44, "dismissal has a touch-sized target");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const bannerBounds = await banner.boundingBox();
    const navigationBounds = await page.locator(".mobile-nav").boundingBox();
    assert.ok(
      bannerBounds &&
        navigationBounds &&
        bannerBounds.y + bannerBounds.height <= navigationBounds.y - 8,
      "the invitation leaves mobile navigation accessible",
    );
    const signup = new URL(
      await banner.getByRole("link", { name: "Create account" }).getAttribute("href"),
      page.url(),
    );
    assert.equal(signup.pathname, "/login");
    assert.equal(
      new URL(signup.searchParams.get("return_to"), home).pathname,
      extension ? "/extension/login-complete" : "/latest",
    );
    await page.screenshot({ path: `${output}/signup-mobile.png`, animations: "disabled" });
    await dismiss.focus();
    await page.keyboard.press("Enter");
    await banner.waitFor({ state: "detached" });
    assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), invitationKey), "true");
    await page.goto(href(`/articles/${slugs[0]}`));
    await page.getByRole("button", { name: "Close preview" }).click();
    await page.reload();
    await page.clock.fastForward(40_000);
    assert.equal(await banner.count(), 0, "dismissal survives navigation and reload");
    assert.equal(await preview.count(), 0, "the same refusal silences the automatic Dev Card");

    await page.goto(href("/dev-card"));
    await page.getByRole("button", { name: "Preview your card" }).click();
    await preview.waitFor();
    assert.equal(await banner.count(), 0);
    await preview.getByRole("button", { name: "Dismiss dev card preview" }).click();

    // Exercise the reverse order in a fresh reader session.
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(href("/latest"));
    await page.reload();
    await page.locator('dialog[aria-label="Your dev card preview"]').waitFor({ state: "attached" });
    await page.clock.fastForward(35_000);
    await preview.waitFor();
    await preview.getByRole("button", { name: "Dismiss dev card preview" }).click();
    await page.evaluate(
      (seen) => sessionStorage.setItem("devfeed:signup-nudge-articles", JSON.stringify(seen)),
      slugs,
    );
    await page.reload();
    await page.clock.fastForward(40_000);
    assert.equal(await banner.count(), 0, "dismissing the card also silences signup");
    assert.equal(await preview.count(), 0);
    assert.deepEqual(errors, []);
    console.log(
      "Signup invitations: mutual dismissal, dialog priority, and explicit preview passed.",
    );
  } finally {
    await page.close();
  }
}
