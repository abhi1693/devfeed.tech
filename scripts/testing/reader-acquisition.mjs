import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const storageKey = "devfeed:reader-acquisition:v1";
const measurementHeader = "x-devfeed-reader-measurement";
const client = "123456.789012";
const session = 1720000000;
export async function checkReaderAcquisition(
  page,
  destination,
  output,
  { extension = false, requests = [], origin, loginServer } = {},
) {
  await mkdir(output, { recursive: true });
  await page.context().route("https://www.googletagmanager.com/gtag/js?**", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `window.gtag = function(command,id,field,callback) {
      if (command === 'get' && !window.__devfeedBlockMeasurement)
        callback(field === 'client_id' ? '${client}' : ${session});
    }; for (const command of window.dataLayer || []) window.gtag(...command);`,
    }),
  );
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  if (!extension) {
    await page.goto(
      destination(
        "/?utm_source=google&utm_medium=organic&utm_campaign=organic-launch&private=private@example.test",
      ),
    );
    await page.locator(".article-card").first().waitFor();
    await page.waitForFunction((key) => sessionStorage.getItem(key), storageKey);
    const initial = await page.evaluate(
      (key) => JSON.parse(sessionStorage.getItem(key)),
      storageKey,
    );
    assert.equal(initial.source, "google");
    assert.equal(initial.medium, "organic");
    assert.equal(initial.campaign, "organic-launch");
    assert.equal(initial.landing_route, "home");
    assert.equal(JSON.stringify(initial).includes("private"), false);
    assert.equal(
      await page.locator("script[data-devfeed-ga]").count(),
      0,
      "capture preserves GA deferral",
    );
    await page.mouse.click(700, 10);
    await page.waitForFunction(() => typeof window.gtag === "function");
    await page.goto(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    assert.deepEqual(
      await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)), storageKey),
      initial,
    );
    await page.getByRole("link", { name: "Sign in", exact: true }).click();
    await page.getByRole("link", { name: "Continue to sign in", exact: true }).waitFor();
    await page.mouse.click(700, 10);
    await page.waitForFunction(() => document.cookie.includes("devfeed_user_acquisition="));
    await page.waitForFunction(() => {
      const value = document.cookie
        .split("; ")
        .find((part) => part.startsWith("devfeed_user_acquisition="));
      return (
        value &&
        JSON.parse(decodeURIComponent(value.split("=").slice(1).join("="))).measurement ===
          "eligible"
      );
    });
    const cookie = await page.evaluate(() => document.cookie);
    const measurement = JSON.parse(
      decodeURIComponent(
        cookie
          .split("; ")
          .find((part) => part.startsWith("devfeed_user_acquisition="))
          .split("=")
          .slice(1)
          .join("="),
      ),
    );
    assert.equal(measurement.source, "google");
    assert.equal(measurement.client_id, client);
    assert.equal(measurement.session_id, session);
    await page.screenshot({ path: `${output}/login.png`, animations: "disabled" });
    const response = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/v1/user/auth/login",
    );
    await page.getByRole("link", { name: "Continue to sign in", exact: true }).click();
    assert.equal((await response).status(), 302);
    await page.getByText("Sign-in provider", { exact: true }).waitFor();
    assert.ok(
      requests.some(
        (item) =>
          item.path === "/v1/user/auth/login" && item.cookie?.includes("devfeed_user_acquisition="),
      ),
      "login context crosses the real Next gateway",
    );

    // A blocked tag must never hold the durable original-open request behind analytics.
    await page
      .context()
      .addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
    await page.goto(destination("/latest"));
    await page.locator(".card-open-link").first().click();
    await page.locator("#article-preview-title").waitFor();
    await page.evaluate(() => {
      window.__devfeedBlockMeasurement = true;
    });
    const read = page.locator(".preview-read-button");
    const original = new URL(await read.getAttribute("href"));
    await page
      .context()
      .route(original.href, (route) =>
        route.fulfill({ contentType: "text/html", body: "<h1>Original publisher</h1>" }),
      );
    await read.evaluate((anchor) => {
      anchor.target = "_self";
    });
    await read.click();
    await page.getByRole("heading", { name: "Original publisher", exact: true }).waitFor();
    for (
      let attempt = 0;
      attempt < 100 && !requests.some((item) => item.path.endsWith("/open"));
      attempt++
    )
      await new Promise((resolve) => setTimeout(resolve, 25));
    assert.ok(
      requests.some((item) => item.path.endsWith("/open")),
      `same-tab navigation reaches the first-party original-open API even without GA callbacks: ${JSON.stringify(requests)}`,
    );
  } else {
    await page.goto(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    await page.waitForFunction(
      () =>
        localStorage.getItem("devfeed:extension-analytics-client") &&
        sessionStorage.getItem("devfeed:reader-acquisition:v1"),
    );
    const firstTouch = await page.evaluate((key) => sessionStorage.getItem(key), storageKey);
    assert.equal(JSON.parse(firstTouch).campaign, "organic-launch");
    await page.goto(destination("/latest?utm_source=github&utm_medium=referral"));
    await page.reload();
    await page.locator(".article-card").first().waitFor();
    await page.waitForFunction((key) => sessionStorage.getItem(key), storageKey);
    assert.equal(
      await page.evaluate((key) => sessionStorage.getItem(key), storageKey),
      firstTouch,
      "hard reload never replaces the initial acquisition with a later URL",
    );
    const signIn = page.getByRole("link", { name: "Sign in", exact: true });
    await signIn.focus();
    const href = new URL(await signIn.getAttribute("href"));
    assert.equal(href.pathname, "/login");
    assert.equal(href.searchParams.get("return_to"), "/extension/login-complete");
    const handoff = JSON.parse(decodeURIComponent(href.hash.slice("#acquisition=".length)));
    const identity = await page.evaluate(() => ({
      client: localStorage.getItem("devfeed:extension-analytics-client"),
      session: JSON.parse(localStorage.getItem("devfeed:extension-analytics-session")),
    }));
    assert.equal(handoff.client_id, identity.client);
    assert.equal(handoff.session_id, identity.session.id);
    assert.equal(handoff.measurement, "eligible");
    assert.equal(href.search.includes(identity.client), false);
    assert.equal(
      handoff.client_platform,
      process.env.DEVFEED_EXTENSION_BROWSER === "edge" ? "edge_extension" : "chrome_extension",
    );
    await page.screenshot({ path: `${output}/extension-handoff.png`, animations: "disabled" });
    const popupReady = page.context().waitForEvent("page");
    await signIn.click();
    const popup = await popupReady;
    await popup.getByRole("link", { name: "Continue to sign in", exact: true }).waitFor();
    assert.equal(
      new URL(popup.url()).hash,
      "",
      "the actual built login consumes the handoff fragment",
    );
    assert.equal(
      await popup.locator("script[data-devfeed-ga]").count(),
      0,
      "extension signups do not initialize disabled web GA",
    );
    const forwarded = await popup.evaluate(() => {
      const cookie = document.cookie
        .split("; ")
        .find((part) => part.startsWith("__Host-devfeed_user_acquisition="));
      return cookie && JSON.parse(decodeURIComponent(cookie.slice(cookie.indexOf("=") + 1)));
    });
    assert.equal(forwarded.client_id, identity.client);
    assert.equal(forwarded.session_id, identity.session.id);
    assert.equal(forwarded.client_platform, handoff.client_platform);
    assert.equal(forwarded.campaign, "organic-launch");
    await popup.screenshot({ path: `${output}/extension-login.png`, animations: "disabled" });
    await popup.getByRole("link", { name: "Continue to sign in", exact: true }).click();
    await popup.getByText("Sign-in provider", { exact: true }).waitFor();
    assert.ok(
      loginServer.logins.some((item) => item.cookie?.includes("__Host-devfeed_user_acquisition=")),
      "the extension's cookie crosses the actual Next login gateway",
    );
    await popup.close();
    await page.evaluate(() => localStorage.setItem("devfeed:extension-analytics-disabled", "true"));
    await signIn.focus();
    await signIn.dispatchEvent("pointerdown");
    const disabled = new URL(await signIn.getAttribute("href"));
    const optedOut = JSON.parse(decodeURIComponent(disabled.hash.slice("#acquisition=".length)));
    assert.equal(optedOut.measurement, "disabled");
    assert.equal(optedOut.status, "unavailable");
    assert.equal(optedOut.client_id, undefined);
    assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), storageKey), null);
  }
}
