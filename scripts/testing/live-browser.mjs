import assert from "node:assert/strict";
import { chromium } from "playwright";

const origin = process.argv[2];
assert.equal(new URL(origin).hostname, "127.0.0.1");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
await context.tracing.start({ screenshots: true, snapshots: true });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
try {
  await page.goto(`${origin}/login?return_to=/settings/profile`);
  await page.getByRole("link", { name: "Continue to sign in", exact: true }).click();
  await page.waitForURL(`${origin}/settings/profile`);
  const identity = await context.request.get(`${origin}/api/v1/user/auth/me`);
  assert.equal(identity.status(), 200);
  const user = await identity.json();
  assert.equal(user.subject, "browser-ci-reader");
  assert.ok((await context.cookies()).some((cookie) => cookie.name === "devfeed_user_session"));

  await page.getByLabel("Display name", { exact: true }).fill("CI persistent reader");
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/user/settings/profile") &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  assert.equal((await saved).status(), 200);
  await page.reload();
  await page.getByLabel("Display name", { exact: true }).waitFor();
  assert.equal(
    await page.getByLabel("Display name", { exact: true }).inputValue(),
    "CI persistent reader",
  );

  const denied = await context.request.put(`${origin}/api/v1/user/settings/profile`, {
    headers: { Origin: origin },
    data: { display_name: "Must not persist" },
  });
  assert.equal(denied.status(), 403, "A profile write without the CSRF token must fail");
  console.log("Browser login and durable profile update passed");
} catch (error) {
  await page.screenshot({ path: "reports/live-browser/failure.png", fullPage: true });
  throw error;
} finally {
  await context.tracing.stop({ path: "reports/live-browser/trace.zip" });
  await browser.close();
}
