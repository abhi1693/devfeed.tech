import assert from "node:assert/strict";
import sharp from "sharp";

export function avatarFixtureVariants(
  base = "https://images.example.test/avatars/browser",
  revision = "public",
) {
  return [32, 64, 128, 256, 512].map((width) => ({
    width,
    url: `${base}/${width}.webp?v=${revision}`,
  }));
}

export async function avatarFixtureImage(width) {
  return sharp({ create: { width, height: width, channels: 3, background: "#7167d9" } })
    .webp()
    .toBuffer();
}

export async function checkPublicAvatar(page) {
  await page.waitForFunction(
    () => document.querySelector(".public-profile-avatar-row img")?.naturalWidth > 0,
  );
  const image = page.locator(".public-profile-avatar-row img");
  assert.match(await image.evaluate((node) => node.currentSrc), /\/128\.webp\?v=/);
  assert.match(await image.getAttribute("srcset"), /32w,.*512w/);
}

// Exercise the shared UI through each built reader's actual browser transport.
export async function checkAvatarUploads(page, screenshotPrefix, attachHandler) {
  const original = await page.evaluate(async () => {
    const url = location.protocol === "chrome-extension:" ? "https://devfeed.tech" : "";
    return (await fetch(`${url}/api/v1/user/settings/profile`, { credentials: "include" })).json();
  });
  let profile = { ...original };
  let uploads = 0;
  let rejectUpload = false;
  const path = "**/api/v1/user/settings/profile{,/avatar}";
  const images = "https://images.example.test/avatars/browser/*";
  const imageHandler = async (route) => {
    const width = Number(new URL(route.request().url()).pathname.split("/").at(-1).split(".")[0]);
    const body = await avatarFixtureImage(width);
    return route.fulfill({
      contentType: "image/webp",
      headers: { "Access-Control-Allow-Origin": "*" },
      body,
    });
  };
  const handler = async (route) => {
    const request = route.request();
    if (request.url().endsWith("/avatar")) {
      const headers = await request.allHeaders();
      assert.ok(headers["x-csrf-token"], "avatar changes carry CSRF protection");
      if (page.url().startsWith("chrome-extension:"))
        assert.match(
          headers.cookie,
          /devfeed_user_session=test-session/,
          "extension uploads carry the real session cookie",
        );
      if (request.method() === "POST") {
        uploads++;
        assert.match(headers["content-type"], /^multipart\/form-data; boundary=/);
        assert.ok(request.postDataBuffer().includes(Buffer.from('name="file"')));
        if (rejectUpload) return route.fulfill({ status: 503, json: {} });
        const variants = avatarFixtureVariants(undefined, uploads);
        profile = { ...profile, avatar_url: variants[3].url, avatar_variants: variants };
      } else {
        assert.equal(request.method(), "DELETE");
        profile = { ...profile, avatar_url: null, avatar_variants: [] };
      }
    }
    return route.fulfill({ json: profile });
  };
  if (attachHandler) attachHandler(handler);
  else await page.route(path, handler);
  await page.route(images, imageHandler);
  try {
    const input = page.getByLabel("Avatar image", { exact: true });
    const bio = page.getByLabel("Short bio", { exact: true });
    const originalBio = await bio.inputValue();
    await bio.fill("Keep this unsaved draft.");
    await input.setInputFiles({
      name: "unsafe.svg",
      mimeType: "image/svg+xml",
      buffer: Buffer.from("<svg/>"),
    });
    await page.getByText("Choose a JPEG, PNG or WebP image.", { exact: true }).waitFor();
    assert.equal(uploads, 0);
    const buffer = await sharp({
      create: { width: 100, height: 60, channels: 3, background: "#7167d9" },
    })
      .png()
      .toBuffer();
    // Valid PNG with trailing padding also proves the gateway permits avatar
    // uploads larger than its ordinary 1 MB JSON request limit.
    const file = {
      name: "avatar.png",
      mimeType: "image/png",
      buffer: Buffer.concat([buffer, Buffer.alloc(1_100_000)]),
    };
    await input.setInputFiles(file);
    await page.getByText("Avatar updated.", { exact: true }).waitFor();
    assert.equal(await bio.inputValue(), "Keep this unsaved draft.");
    const first = await page.getByLabel("Avatar URL", { exact: true }).inputValue();
    await page.waitForFunction(() => {
      const image = document.querySelector(".user-menu-trigger .profile-avatar img");
      return image?.naturalWidth > 0 && image.currentSrc.includes("?v=1");
    });
    const menuImage = page.locator(".user-menu-trigger .profile-avatar img");
    assert.ok(
      await menuImage.evaluate((image) => image.naturalWidth <= 128),
      "menu loads a small avatar variant",
    );
    // Card artwork uses the canonical 256px image.
    assert.ok(await page.locator('.dev-card-preview image[href*="256.webp?v=1"]').count());
    await input.setInputFiles(file);
    await page.waitForFunction(() =>
      document.querySelector("#profile-avatar")?.value?.includes("?v=2"),
    );
    const second = await page.getByLabel("Avatar URL", { exact: true }).inputValue();
    assert.notEqual(second, first);
    assert.equal(new URL(second).pathname, new URL(first).pathname);
    rejectUpload = true;
    await input.setInputFiles(file);
    await page.getByText("Couldn’t update your avatar. Try again.", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Avatar URL", { exact: true }).inputValue(), second);
    rejectUpload = false;
    await page.getByRole("button", { name: "Discard changes", exact: true }).click();
    assert.equal(await bio.inputValue(), originalBio);
    assert.equal(await page.getByLabel("Avatar URL", { exact: true }).inputValue(), second);
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download card", exact: true }).click();
    assert.equal(await (await download).failure(), null);
    await page.getByText("Your card is downloaded.", { exact: true }).waitFor();
    const viewport = page.viewportSize();
    await page.setViewportSize({ width: 320, height: 844 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page
      .locator(".profile-field-grid")
      .screenshot({ path: `${screenshotPrefix}-mobile.png` });
    await page.setViewportSize(viewport);
    await page.getByRole("button", { name: "Remove", exact: true }).click();
    await page.getByText("Avatar removed.", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Avatar URL", { exact: true }).inputValue(), "");
    await page.waitForFunction(
      () => !document.querySelector(".user-menu-trigger .profile-avatar img"),
    );
  } finally {
    if (attachHandler) attachHandler(null);
    else await page.unroute(path, handler);
    await page.unroute(images, imageHandler);
    await page.reload();
    await page.getByLabel("Display name", { exact: true }).waitFor();
  }
}
