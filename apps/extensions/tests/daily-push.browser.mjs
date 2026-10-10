import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { chromium } from "playwright";

const browser = process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome";
const extension = path.resolve(import.meta.dirname, `../dist/${browser}`);
const newTab = browser === "edge" ? "edge://newtab" : "chrome://newtab";

test(
  "daily browser notifications are managed on the website from the built extension",
  {
    timeout: 60000,
  },
  async () => {
    const manifest = JSON.parse(await readFile(path.join(extension, "manifest.json"), "utf8"));
    assert.ok(!manifest.permissions?.includes("notifications"));
    assert.ok(!manifest.permissions?.includes("gcm"));
    const profile = await mkdtemp(path.join(tmpdir(), `devfeed-push-${browser}-`));
    let context;
    const errors = [];
    const pushRequests = [];
    try {
      context = await chromium.launchPersistentContext(profile, {
        executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? undefined
          : browser === "edge"
            ? "msedge"
            : "chromium",
        headless: true,
        viewport: { width: 1280, height: 900 },
        args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
      });
      await context.addInitScript(() => {
        window.__pushPermissionRequests = 0;
        window.__pushWorkerRegistrations = 0;
        if ("Notification" in window) {
          Notification.requestPermission = async () => {
            window.__pushPermissionRequests++;
            return "granted";
          };
        }
        if ("serviceWorker" in navigator) {
          navigator.serviceWorker.register = async () => {
            window.__pushWorkerRegistrations++;
            throw new Error("An extension must enroll push on the website origin");
          };
        }
      });
      await context.route("https://devfeed.tech/settings/notifications", (route) =>
        route.fulfill({ contentType: "text/html", body: "<h1>Website notification settings</h1>" }),
      );
      await context.route("https://devfeed.tech/api/**", (route) => {
        const url = new URL(route.request().url());
        const endpoint = url.pathname.replace("/api/v1/user/", "");
        if (endpoint.startsWith("notifications/push/")) pushRequests.push(url.pathname);
        let json = {};
        if (endpoint === "auth/me")
          json = {
            user_id: "11111111-1111-4111-8111-111111111111",
            name: "Reader",
            email: "reader@example.test",
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            csrf_token: "test",
          };
        else if (endpoint === "settings/profile")
          json = { display_name: "Reader", username: "reader", avatar_url: null, stack: [] };
        else if (endpoint === "settings/notifications") json = { show_badge: true, sound: false };
        else if (endpoint === "settings/appearance") json = { theme: "light" };
        else if (endpoint === "settings/feed") json = { languages: ["en"] };
        else if (endpoint === "notifications/config") json = { enabled: false };
        else if (endpoint === "auth/config") json = { enabled: true, providers: [] };
        else if (endpoint === "must-reads")
          json = {
            date: new Date().toISOString().slice(0, 10),
            timezone: "UTC",
            items: [],
            reasons: {},
            read_ids: [],
            presented: true,
            preparing: false,
          };
        else if (endpoint === "preferences") json = { topic_ids: [] };
        else if (endpoint === "preferences/sources") json = { source_ids: [] };
        else if (url.pathname === "/api/v1/topics" || url.pathname === "/api/v1/sources")
          json = { items: [], next_cursor: null };
        else if (url.pathname === "/api/v1/user/engagement") json = [];
        else if (endpoint === "feed" || url.pathname === "/api/v1/feed")
          json = {
            items: [],
            next_cursor: null,
            has_interests: true,
            status: "ready",
            reasons: {},
          };
        return route.fulfill({ json });
      });
      const page = await context.newPage();
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(newTab);
      await page.waitForURL(/^chrome-extension:/);
      const extensionBase = page.url().split("#")[0];
      await page.goto(`${extensionBase}#/settings/notifications`);
      const section = page.getByRole("region", { name: "Daily must-read browser notifications" });
      await section.getByRole("heading", { name: "Browser notifications", exact: true }).waitFor();
      await section.getByText(/One personalized must-read article each day\./).waitFor();
      const link = section.getByRole("link", { name: "Manage browser notifications", exact: true });
      assert.equal(await link.getAttribute("href"), "https://devfeed.tech/settings/notifications");
      assert.equal(await link.getAttribute("target"), "_blank");
      assert.match(await link.getAttribute("rel"), /noopener/);
      assert.equal(await section.getByRole("button").count(), 0);
      await page.setViewportSize({ width: 375, height: 812 });
      assert.equal(await link.isVisible(), true);
      const popupPromise = context.waitForEvent("page");
      await link.click();
      const popup = await popupPromise;
      await popup.waitForURL("https://devfeed.tech/settings/notifications");
      await popup.getByRole("heading", { name: "Website notification settings" }).waitFor();
      assert.equal(await popup.evaluate(() => window.opener), null);
      assert.deepEqual(
        await page.evaluate(() => ({
          permissions: window.__pushPermissionRequests,
          registrations: window.__pushWorkerRegistrations,
        })),
        { permissions: 0, registrations: 0 },
      );
      assert.deepEqual(
        pushRequests,
        [],
        "The extension does not fetch or enroll browser push subscriptions",
      );
      assert.deepEqual(errors, []);
    } finally {
      await context?.close();
      await rm(profile, { recursive: true, force: true });
    }
  },
);
