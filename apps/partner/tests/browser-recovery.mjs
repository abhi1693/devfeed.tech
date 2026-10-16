import assert from "node:assert/strict";

const heading = "Could not load your partner portal";
const privateDetail = "private-partner-service-diagnostic";

async function assertErrorLayout(page, { width, theme }) {
  const main = page.getByRole("main", { name: heading, exact: true });
  const card = page.getByRole("region", { name: heading, exact: true });
  await card.waitFor();
  await page.waitForFunction(
    (dark) => document.documentElement.classList.contains("dark") === dark,
    theme === "dark",
  );
  await main.getByRole("link", { name: "DevFeed Partners home", exact: true }).waitFor();
  const retry = main.getByRole("link", { name: "Try again", exact: true });
  await retry.waitFor();
  assert.equal(await retry.getAttribute("href"), "", "Retry preserves the current URL natively");
  const geometry = await card.evaluate((element) => {
    const rect = element.getBoundingClientRect().toJSON();
    const style = getComputedStyle(element);
    const title = element.querySelector("h1");
    const retry = element.querySelector(".shared-retry-button");
    return {
      card: rect,
      frame: element.parentElement.getBoundingClientRect().toJSON(),
      main: element.closest("main").getBoundingClientRect().toJSON(),
      retry: retry.getBoundingClientRect().toJSON(),
      radius: parseFloat(style.borderRadius),
      border: parseFloat(style.borderTopWidth),
      titleSize: parseFloat(getComputedStyle(title).fontSize),
      background: getComputedStyle(document.body).backgroundColor,
      foreground: getComputedStyle(title).color,
      dark: document.documentElement.classList.contains("dark"),
      viewportHeight: innerHeight,
      pageWidth: document.documentElement.scrollWidth,
    };
  });
  const label = `${width}px/${theme}: server error`;
  assert.equal(geometry.dark, theme === "dark", `${label}: saved theme applies before recovery`);
  assert.ok(geometry.main.height >= geometry.viewportHeight, `${label}: full-height layout`);
  assert.ok(
    Math.abs(geometry.card.x + geometry.card.width / 2 - width / 2) <= 2,
    `${label}: the branded card is horizontally centered`,
  );
  assert.ok(
    Math.abs(geometry.frame.y + geometry.frame.height / 2 - geometry.viewportHeight / 2) <= 2,
    `${label}: the brand and error are vertically centered together`,
  );
  assert.ok(geometry.card.width <= 480, `${label}: copy remains readable on wide screens`);
  assert.ok(geometry.card.x >= 16 && geometry.card.right <= width - 16);
  assert.ok(geometry.radius > 0 && geometry.border > 0, `${label}: card styles are loaded`);
  assert.ok(geometry.titleSize >= 20, `${label}: heading styles are loaded`);
  assert.ok(geometry.retry.height >= 36 && geometry.retry.width >= 100);
  assert.ok(geometry.pageWidth <= width, `${label}: no horizontal overflow`);
  const background = geometry.background.match(/\d+/g).slice(0, 3).map(Number);
  const foreground = geometry.foreground.match(/\d+/g).slice(0, 3).map(Number);
  assert.ok(
    theme === "dark"
      ? background.every((value) => value < 80) && foreground.every((value) => value > 180)
      : background.every((value) => value > 230) && foreground.every((value) => value < 80),
    `${label}: text and background inherit the theme`,
  );
  const text = await main.innerText();
  assert.doesNotMatch(
    text,
    /private-partner-service-diagnostic|private-query-marker|outside\.invalid/,
  );
  assert.doesNotMatch(text, /test-csrf|devfeed_partner_session/);
  return retry;
}

/** Exercise Next's real server-error boundary with disposable authenticated API failures. */
export async function assertPartnerRecoveryJourneys({
  browser,
  origin,
  root,
  productName,
  unavailable,
  requestCount,
}) {
  const route =
    "/products?q=API+Checker&days=7&product_offset=0&debug=private-query-marker" +
    "&return_to=https%3A%2F%2Foutside.invalid%2Fnever-navigate";
  for (const width of [1440, 390]) {
    for (const theme of ["light", "dark"]) {
      const path = theme === "light" ? "/v1/partner/accounts" : "/v1/partner/auth/me";
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 900 },
        colorScheme: theme,
        storageState: {
          cookies: [
            {
              name: "devfeed_partner_session",
              value: "partner",
              domain: "127.0.0.1",
              path: "/",
              httpOnly: true,
              secure: false,
              sameSite: "Lax",
            },
          ],
          origins: [{ origin, localStorage: [{ name: "devfeed:theme", value: theme }] }],
        },
      });
      const page = await context.newPage();
      const label = `${theme}-${width}`;
      try {
        unavailable(path, privateDetail);
        const beforeInitial = requestCount(path);
        await page.goto(origin + route, { waitUntil: "domcontentloaded" });
        await page.getByRole("heading", { name: heading, exact: true }).waitFor();
        assert.ok(requestCount(path) > beforeInitial, "The initial server session request failed");
        let retry = await assertErrorLayout(page, { width, theme });
        assert.equal(page.url(), origin + route);
        await page.evaluate(() => {
          window.__partnerRecoveryDocument = "initial-failure";
        });
        const beforeRetry = requestCount(path);
        await retry.focus();
        await Promise.all([
          page.waitForNavigation({ waitUntil: "domcontentloaded" }),
          page.keyboard.press("Enter"),
        ]);
        await page.getByRole("heading", { name: heading, exact: true }).waitFor();
        assert.ok(requestCount(path) > beforeRetry, "A failed Retry makes a fresh server request");
        assert.equal(await page.evaluate(() => window.__partnerRecoveryDocument), undefined);
        assert.equal(page.url(), origin + route, "A failed Retry retains the full route and query");
        retry = await assertErrorLayout(page, { width, theme });
        await page.screenshot({ path: `${root}reports/partner/error-retry-${label}.png` });
        await page.evaluate(() => {
          window.__partnerRecoveryDocument = "repeated-failure";
        });
        unavailable(null);
        const beforeRecovery = requestCount(path);
        const [recoveredDocument] = await Promise.all([
          page.waitForNavigation({ waitUntil: "domcontentloaded" }),
          retry.click(),
        ]);
        assert.equal(
          recoveredDocument.url(),
          origin + route,
          "The fresh document request preserves the original route and query",
        );
        await page.getByRole("heading", { name: "Products", exact: true, level: 1 }).waitFor();
        await page.getByText(productName, { exact: true }).first().waitFor();
        assert.ok(
          requestCount(path) > beforeRecovery,
          "Healthy Retry fetches current session data",
        );
        assert.equal(await page.evaluate(() => window.__partnerRecoveryDocument), undefined);
        const recovered = new URL(page.url());
        assert.equal(recovered.origin + recovered.pathname, origin + "/products");
        assert.equal(recovered.searchParams.get("q"), "API Checker");
        assert.equal(recovered.searchParams.get("days"), "7");
        assert.equal(await page.getByRole("heading", { name: heading, exact: true }).count(), 0);
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          false,
        );
        await page.screenshot({ path: `${root}reports/partner/error-recovered-${label}.png` });
      } catch (error) {
        const themeState = () => ({
          saved: localStorage.getItem("devfeed:theme"),
          classes: document.documentElement.className,
          scheme: getComputedStyle(document.documentElement).colorScheme,
          systemDark: matchMedia("(prefers-color-scheme: dark)").matches,
          themeBootstrap: [...document.scripts].some((script) =>
            script.textContent.includes("devfeed:theme"),
          ),
        });
        const immediate = await page.evaluate(themeState);
        await page.waitForTimeout(500);
        console.error("Partner recovery theme state", {
          label,
          path,
          immediate,
          settled: await page.evaluate(themeState),
        });
        await page.screenshot({
          path: `${root}reports/partner/error-recovery-failure-${label}.png`,
        });
        throw error;
      } finally {
        unavailable(null);
        await context.close();
      }
    }
  }
  console.log(
    "Partner server-error recovery passed: repeated failures, fresh server requests, preserved routes, and branded light/dark desktop/mobile layouts.",
  );
}
