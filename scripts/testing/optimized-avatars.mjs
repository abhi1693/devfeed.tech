import assert from "node:assert/strict";
import { avatarFixtureImage } from "./avatar-uploads.mjs";

export const githubAvatarFixture = "https://avatars.githubusercontent.com/u/5083532?v=4";
export async function mockOptimizedAvatars(page) {
  await page.route("**/api/avatars/github/5083532/*", async (route) => {
    const size = Number(new URL(route.request().url()).pathname.split("/").at(-1));
    return route.fulfill({ contentType: "image/webp", body: await avatarFixtureImage(size) });
  });
}
export async function checkOptimizedPublicAvatar(page) {
  const image = page.locator(".public-profile-avatar-row img");
  await image.waitFor();
  await page.waitForFunction(
    () => document.querySelector(".public-profile-avatar-row img")?.naturalWidth > 0,
  );
  assert.match(
    await image.evaluate((node) => node.currentSrc),
    /\/api\/avatars\/github\/5083532\/128\?v=4$/,
  );
  assert.equal(await image.getAttribute("sizes"), "(max-width: 540px) 84px, 104px");
  assert.equal(await image.evaluate((node) => node.getBoundingClientRect().width), 104);
  assert.match(await image.getAttribute("srcset"), /\/192\?v=4 192w/);
}
