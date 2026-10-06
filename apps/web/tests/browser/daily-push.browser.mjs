import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const endpoint = "https://fcm.googleapis.com/fcm/send/fixture-daily-must-read";
const subscriptionId = "22222222-2222-4222-8222-222222222222";
const firstConsentId = "33333333-3333-4333-8333-333333333333";
const nextConsentId = "44444444-4444-4444-8444-444444444444";
const expiringConsentId = "55555555-5555-4555-8555-555555555555";
const switchingConsentId = "66666666-6666-4666-8666-666666666666";
const firstUserId = "11111111-1111-4111-8111-111111111111";
const secondUserId = "77777777-7777-4777-8777-777777777777";
const notificationIds = Array.from(
  { length: 20 },
  (_, index) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(index + 1).padStart(12, "0")}`,
);
const publicKey = Buffer.from([4, ...Array(64).fill(1)]).toString("base64url");

async function mockBrowserPush(context) {
  await context.addInitScript(
    ({ endpoint }) => {
      window.__pushMetrics = { permissions: [], subscribes: 0, unsubscribes: 0, registrations: 0 };
      window.__pushPermissionResult = "granted";
      Object.defineProperty(Notification, "permission", {
        configurable: true,
        get: () => localStorage.getItem("fixture:push-permission") ?? "default",
      });
      Notification.requestPermission = async () => {
        window.__pushMetrics.permissions.push({ activated: navigator.userActivation.isActive });
        localStorage.setItem("fixture:push-permission", window.__pushPermissionResult);
        return window.__pushPermissionResult;
      };
      const subscription = () => ({
        endpoint,
        expirationTime: null,
        options: { applicationServerKey: new Uint8Array([4, ...Array(64).fill(1)]).buffer },
        toJSON: () => ({
          endpoint,
          expirationTime: null,
          keys: { p256dh: "fixture-encryption-key", auth: "fixture-auth-key" },
        }),
        unsubscribe: async () => {
          window.__pushMetrics.unsubscribes++;
          localStorage.removeItem("fixture:push-subscription");
          return true;
        },
      });
      PushManager.prototype.getSubscription = async () =>
        localStorage.getItem("fixture:push-subscription") ? subscription() : null;
      PushManager.prototype.subscribe = async (options) => {
        if (Notification.permission !== "granted")
          throw new Error("Permission must precede enrollment");
        if (!options.userVisibleOnly || options.applicationServerKey.byteLength !== 65)
          throw new Error("Expected user-visible VAPID enrollment");
        window.__pushMetrics.subscribes++;
        localStorage.setItem("fixture:push-subscription", "true");
        return subscription();
      };
      const register = navigator.serviceWorker.register.bind(navigator.serviceWorker);
      navigator.serviceWorker.register = (...args) => {
        window.__pushMetrics.registrations++;
        return register(...args);
      };
    },
    { endpoint },
  );
}

async function dispatchWorkerEvent(worker, type, payload, preserveClients = false) {
  return worker.evaluate(
    async ({ type, payload, preserveClients }) => {
      self.__pushNotifications ??= [];
      self.__pushNavigations ??= [];
      self.registration.showNotification = async (title, options) => {
        if (self.__pushFailNextDisplay) {
          self.__pushFailNextDisplay = false;
          throw new Error("Fixture system notification failure");
        }
        self.__pushNotifications.push({ title, options });
      };
      self.registration.getNotifications = async () => [];
      if (!preserveClients)
        self.clients.matchAll = async () => [
          {
            url: `${self.location.origin}/settings/notifications`,
            navigate: async (url) => {
              self.__pushNavigations.push(url);
              return { url };
            },
            focus: async () => {},
          },
        ];
      const jobs = [];
      const event = new Event(type);
      Object.defineProperty(event, "waitUntil", { value: (job) => jobs.push(job) });
      if (type === "push") Object.defineProperty(event, "data", { value: { json: () => payload } });
      else
        Object.defineProperty(event, "notification", { value: { data: payload, close: () => {} } });
      self.dispatchEvent(event);
      await Promise.all(jobs);
      return { notifications: self.__pushNotifications, navigations: self.__pushNavigations };
    },
    { type, payload, preserveClients },
  );
}

async function writeWorkerConsent(page, subscriptionId, accountId) {
  return page.evaluate(
    async ({ subscriptionId, accountId }) => {
      const registration = await navigator.serviceWorker.getRegistration("/");
      const channel = new MessageChannel();
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          channel.port1.close();
          reject(new Error("Consent ACK timed out"));
        }, 10000);
        channel.port1.onmessage = ({ data }) => {
          clearTimeout(timer);
          channel.port1.close();
          if (data.ok) resolve();
          else reject(new Error("Consent write failed"));
        };
        registration.active.postMessage(
          {
            type: "devfeed:push-consent",
            subscription_id: subscriptionId,
            account_id: accountId,
          },
          [channel.port2],
        );
      });
    },
    { subscriptionId, accountId },
  );
}

async function checkClickRevocationOrder(page, worker, articleUrl) {
  const order = [];
  let click;
  let revoke;
  await worker.evaluate(() => {
    self.__raceOriginalMatchAll = self.clients.matchAll;
    self.__raceOriginalNavigations = [...self.__pushNavigations];
    self.__raceClientLookupStarted = false;
    self.__raceClientLookupGate = new Promise((resolve) => {
      self.__raceReleaseClients = resolve;
    });
    self.clients.matchAll = async (...args) => {
      self.__raceClientLookupStarted = true;
      await self.__raceClientLookupGate;
      return self.__raceOriginalMatchAll(...args);
    };
  });
  try {
    click = dispatchWorkerEvent(
      worker,
      "notificationclick",
      {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      },
      true,
    ).then((result) => {
      order.push("click");
      return result;
    });
    await worker.evaluate(async () => {
      const deadline = Date.now() + 10000;
      while (!self.__raceClientLookupStarted && Date.now() < deadline)
        await new Promise((resolve) => setTimeout(resolve, 10));
      if (!self.__raceClientLookupStarted)
        throw new Error("Click did not reach the paused client lookup");
    });
    revoke = writeWorkerConsent(page, null, null).then(() => {
      order.push("revocation ACK");
    });
    await Promise.race([revoke, new Promise((resolve) => setTimeout(resolve, 150))]);
    assert.deepEqual(
      order,
      [],
      "Revocation cannot acknowledge while an earlier notification click is still navigating",
    );
    await worker.evaluate(() => self.__raceReleaseClients());
    await revoke;
    const clicked = await click;
    assert.ok(order.includes("revocation ACK"));
    assert.equal(clicked.navigations.at(-1), articleUrl);
    const clickedCount = clicked.navigations.length;
    const laterClick = await dispatchWorkerEvent(
      worker,
      "notificationclick",
      {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      },
      true,
    );
    assert.equal(
      laterClick.navigations.length,
      clickedCount,
      "A notification click after the revocation ACK cannot navigate",
    );
  } finally {
    await worker.evaluate(() => self.__raceReleaseClients());
    await Promise.allSettled([click, revoke]);
    await writeWorkerConsent(page, firstConsentId, firstUserId);
    await worker.evaluate(() => {
      self.clients.matchAll = self.__raceOriginalMatchAll;
      self.__pushNavigations = self.__raceOriginalNavigations;
    });
  }
}

test(
  "website browser push requires consent and delivers only the daily must-read",
  {
    timeout: 120000,
  },
  async () => {
    let browser;
    let currentPage;
    let app;
    let logs = "";
    const errors = [];
    const writes = [];
    const apiRequests = [];
    const receipts = [];
    let receiptStatus = 204;
    let receiptStatuses = [];
    let receiptSessionStatus = 200;
    let receiptSessionUserId;
    let receiptGate;
    let receiptGateStarted;
    let saved = [];
    let consentId = firstConsentId;
    let rejectRegistration = false;
    let rejectDisable = false;
    let authenticated = true;
    let currentUserId = firstUserId;
    const fixture = createServer(async (request, response) => {
      try {
        const url = new URL(request.url, "http://localhost");
        const path = url.pathname.replace("/v1/user/", "");
        apiRequests.push({ path, method: request.method });
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        const buffer = Buffer.concat(chunks).toString();
        const payload = buffer ? JSON.parse(buffer) : undefined;
        const send = (json, status = 200) => {
          response.writeHead(status, { "Content-Type": "application/json" });
          response.end(status === 204 ? "" : JSON.stringify(json));
        };
        if (path.startsWith("notifications/push/") && request.method !== "GET") {
          assert.equal(request.headers["x-csrf-token"], "test");
          writes.push({ method: request.method, path, payload });
        }
        if (path === "notifications/push/receipt-session") {
          assert.equal(request.method, "GET");
          if (receiptGate) {
            receiptGateStarted();
            await receiptGate;
          }
          if (!authenticated) return send({ detail: "Unauthorized" }, 401);
          return send(
            { user_id: receiptSessionUserId ?? currentUserId, csrf_token: "test" },
            receiptSessionStatus,
          );
        }
        if (path === "notifications/push/receipts") {
          assert.equal(request.method, "POST");
          assert.deepEqual(Object.keys(payload).sort(), [
            "action",
            "notification_id",
            "subscription_id",
          ]);
          assert.ok(["displayed", "clicked", "opened"].includes(payload.action));
          assert.match(payload.notification_id, /^[a-f0-9-]{36}$/);
          receipts.push(payload);
          return send(undefined, receiptStatuses.shift() ?? receiptStatus);
        }
        if (path === "notifications/push/config")
          return send({
            enabled: true,
            public_key: publicKey,
            delivery_hour: 9,
            delivery_timezone: "America/New_York",
          });
        if (path === "notifications/push/subscriptions") {
          if (request.method === "GET") return send({ subscriptions: saved });
          assert.equal(request.method, "POST");
          assert.equal(payload.endpoint, endpoint);
          assert.ok(["Asia/Kolkata", "Asia/Calcutta"].includes(payload.timezone));
          assert.deepEqual(Object.keys(payload).sort(), ["endpoint", "keys", "timezone"]);
          if (rejectRegistration) return send({ detail: "Unavailable" }, 503);
          saved = [
            {
              id: subscriptionId,
              consent_id: consentId,
              endpoint_hash: createHash("sha256").update(endpoint).digest("hex"),
              enabled: true,
              timezone: payload.timezone,
            },
          ];
          return send({
            id: subscriptionId,
            consent_id: consentId,
            enabled: true,
            timezone: payload.timezone,
          });
        }
        if (path === `notifications/push/subscriptions/${subscriptionId}`) {
          assert.equal(request.method, "DELETE");
          if (rejectDisable) return send({ detail: "Unavailable" }, 503);
          saved = saved.map((item) => ({ ...item, enabled: false }));
          return send(undefined, 204);
        }
        if (path === "auth/me")
          return send(
            authenticated
              ? {
                  user_id: currentUserId,
                  name: "Reader",
                  email: "reader@example.test",
                  expires_at: Math.floor(Date.now() / 1000) + 3600,
                  csrf_token: "test",
                }
              : null,
          );
        if (path === "settings/profile")
          return send({ display_name: "Reader", username: "reader", avatar_url: null, stack: [] });
        if (path === "settings/notifications") return send({ show_badge: true, sound: false });
        if (path === "notifications/config") return send({ enabled: false });
        if (path === "auth/config") return send({ enabled: true, providers: [] });
        if (path === "settings/appearance") return send({ theme: "light" });
        if (path === "settings/feed") return send({ languages: ["en"] });
        if (path === "feed")
          return send({
            status: "ready",
            generation: "fixture-user-home",
            has_interests: true,
            items: [],
            next_cursor: null,
            reasons: {},
          });
        if (path === "must-reads")
          return send({
            date: new Date().toISOString().slice(0, 10),
            timezone: "UTC",
            items: [],
            reasons: {},
            read_ids: [],
            presented: true,
            preparing: false,
          });
        return send({});
      } catch (error) {
        errors.push(error.message);
        response.writeHead(500);
        response.end();
      }
    });
    const probe = createServer();
    await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
    await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
    const port = probe.address().port;
    await new Promise((resolve) => probe.close(resolve));
    const origin = `http://127.0.0.1:${port}`;
    const upstream = `http://127.0.0.1:${fixture.address().port}`;
    try {
      const env = Object.fromEntries(
        Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
      );
      app = spawn(
        process.execPath,
        [
          `${root}/node_modules/next/dist/bin/next`,
          "start",
          "--hostname",
          "127.0.0.1",
          "--port",
          String(port),
        ],
        {
          cwd: `${root}/apps/web`,
          env: {
            ...env,
            DEVFEED_PUBLIC_API_URL: upstream,
            DEVFEED_USER_API_URL: upstream,
            DEVFEED_USER_BASE_URL: origin,
          },
          stdio: "pipe",
        },
      );
      app.stdout.on("data", (data) => (logs += data));
      app.stderr.on("data", (data) => (logs += data));
      let ready = false;
      for (let i = 0; i < 100; i++) {
        try {
          const response = await fetch(`${origin}/login`);
          if (response.ok) {
            ready = true;
            break;
          }
        } catch {}
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.ok(ready, logs);
      browser = await chromium.launch({ headless: true });
      const context = await browser.newContext({
        timezoneId: "Asia/Kolkata",
        viewport: { width: 1280, height: 900 },
      });
      await mockBrowserPush(context);
      const page = await context.newPage();
      currentPage = page;
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`${origin}/settings/notifications`);
      const section = page.getByRole("region", { name: "Daily must-read browser notifications" });
      const enable = section.getByRole("button", {
        name: "Enable notifications on this browser",
        exact: true,
      });
      const disable = section.getByRole("button", {
        name: "Disable notifications on this browser",
        exact: true,
      });
      await enable.waitFor();
      await section.getByText(/One personalized must-read article each day\./).waitFor();
      await section.getByText(/Delivered around 9 AM in America\/New_York/).waitFor();
      assert.deepEqual(await page.evaluate(() => window.__pushMetrics.permissions), []);
      assert.equal(writes.length, 0, "Loading settings does not enroll a browser");
      await enable.click();
      await disable.waitFor();
      await section
        .getByText("Daily must-read notifications are enabled on this browser.", { exact: true })
        .waitFor();
      assert.deepEqual(await page.evaluate(() => window.__pushMetrics.permissions), [
        { activated: true },
      ]);
      assert.equal(await page.evaluate(() => window.__pushMetrics.subscribes), 1);
      assert.equal(writes.filter((item) => item.method === "POST").length, 1);
      const worker = context
        .serviceWorkers()
        .find((item) => item.url() === `${origin}/web-push-sw.js`);
      assert.ok(worker, "Enabling registers the real website notification worker");
      const foreignMessages = await worker.evaluate(() => {
        const jobs = [];
        const event = new MessageEvent("message", {
          origin: "https://attacker.example",
          data: { type: "devfeed:push-consent", subscription_id: null },
        });
        // Even a forged source URL must not replace the message origin check.
        Object.defineProperty(event, "source", {
          value: { url: `${self.location.origin}/settings/notifications` },
        });
        Object.defineProperty(event, "waitUntil", { value: (job) => jobs.push(job) });
        self.dispatchEvent(event);
        return jobs.length;
      });
      assert.equal(foreignMessages, 0, "Foreign messages cannot clear this browser's consent");
      const articleUrl = `${origin}/articles/your-daily-must-read`;
      const dailyPayload = {
        notification: {
          title: "Your daily must-read",
          body: "An article selected for you",
          navigate: articleUrl,
        },
        data: {
          kind: "daily_must_read",
          notification_id: notificationIds[0],
          subscription_id: firstConsentId,
          url: articleUrl,
          expires_at: Math.floor(Date.now() / 1000) + 86400,
        },
      };
      const expired = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, expires_at: Math.floor(Date.now() / 1000) - 1 },
      });
      assert.equal(
        expired.notifications.length,
        0,
        "An expired article cannot display a stale browser alert",
      );
      const wrongKind = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, kind: "topic_article" },
      });
      assert.equal(
        wrongKind.notifications.length,
        0,
        "Other inbox events cannot display browser alerts",
      );
      const wrongAccount = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, subscription_id: "different-account" },
      });
      assert.equal(
        wrongAccount.notifications.length,
        0,
        "Push must match the authenticated browser consent",
      );
      const externalLink = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, url: "https://attacker.example/articles/untrusted" },
      });
      assert.equal(externalLink.notifications.length, 0, "Push cannot redirect outside DevFeed");
      assert.deepEqual(receipts, [], "Rejected pushes do not produce delivery analytics");
      const workerRequestsStart = apiRequests.length;
      const received = await dispatchWorkerEvent(worker, "push", dailyPayload);
      assert.equal(received.notifications.length, 1);
      assert.equal(received.notifications[0].title, "Your daily must-read");
      assert.equal(received.notifications[0].options.body, "An article selected for you");
      assert.equal(received.notifications[0].options.data.url, articleUrl);
      assert.equal(received.notifications[0].options.data.notification_id, notificationIds[0]);
      assert.deepEqual(receipts, [
        {
          notification_id: notificationIds[0],
          subscription_id: firstConsentId,
          action: "displayed",
        },
      ]);
      const duplicate = await dispatchWorkerEvent(worker, "push", dailyPayload);
      assert.equal(
        duplicate.notifications.length,
        1,
        "A retried push displays the daily article once",
      );
      assert.equal(receipts.length, 1, "A duplicate push does not inflate displayed receipts");
      const retryPayload = {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[1] },
      };
      await worker.evaluate(() => {
        self.__pushFailNextDisplay = true;
      });
      await assert.rejects(
        dispatchWorkerEvent(worker, "push", retryPayload),
        /Fixture system notification failure/,
      );
      assert.equal(receipts.length, 1, "A failed system display is not reported as displayed");
      const retried = await dispatchWorkerEvent(worker, "push", retryPayload);
      assert.equal(
        retried.notifications.length,
        2,
        "A display failure releases the daily receipt for a retry",
      );
      assert.equal(receipts.length, 2);
      assert.equal(receipts[1].notification_id, notificationIds[1]);
      assert.equal(receipts[1].action, "displayed");
      const clicked = await dispatchWorkerEvent(worker, "notificationclick", {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      });
      assert.deepEqual(
        clicked.navigations,
        [articleUrl],
        "Notification clicks open the selected DevFeed article",
      );
      assert.deepEqual(
        receipts
          .filter((item) => item.notification_id === notificationIds[0])
          .map((item) => item.action),
        ["displayed", "clicked", "opened"],
      );
      assert.ok(
        apiRequests.slice(workerRequestsStart).every((item) => item.path !== "auth/me"),
        "Worker feedback uses a non-renewing receipt session instead of auth/me",
      );
      const baselineNotifications = [...clicked.notifications];
      const baselineNavigations = [...clicked.navigations];
      const validClick = {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      };
      const receiptCount = receipts.length;
      receiptSessionUserId = secondUserId;
      const ownerMismatch = await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.equal(ownerMismatch.navigations.length, baselineNavigations.length + 1);
      assert.equal(
        receipts.length,
        receiptCount,
        "A different cookie owner cannot report an old account's click",
      );
      const unmatchedOwnerDisplay = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[6] },
      });
      assert.equal(unmatchedOwnerDisplay.notifications.length, baselineNotifications.length + 1);
      assert.equal(
        receipts.length,
        receiptCount,
        "Analytics owner mismatch does not block local display",
      );
      receiptSessionUserId = undefined;
      const afterMismatch = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[7] },
      });
      assert.equal(afterMismatch.notifications.length, baselineNotifications.length + 2);
      assert.equal(
        receipts.at(-1).notification_id,
        notificationIds[7],
        "Feedback failure never clears consent",
      );

      authenticated = false;
      const beforeUnauthorized = receipts.length;
      const unauthorized = await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.equal(unauthorized.navigations.at(-1), articleUrl);
      assert.equal(
        receipts.length,
        beforeUnauthorized,
        "An absent cookie session cannot report receipts",
      );
      authenticated = true;

      receiptStatus = 503;
      const beforeUnavailable = receipts.length;
      const unavailable = await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.equal(
        unavailable.navigations.at(-1),
        articleUrl,
        "Unavailable telemetry cannot block article navigation",
      );
      assert.deepEqual(
        receipts.slice(beforeUnavailable).map((item) => item.action),
        ["clicked", "clicked", "opened", "opened"],
        "Transient receipt failures get one bounded retry per action",
      );
      receiptStatus = 204;
      receiptStatuses = [429, 204, 204];
      const beforeRateLimited = receipts.length;
      await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.deepEqual(
        receipts.slice(beforeRateLimited).map((item) => item.action),
        ["clicked", "clicked", "opened"],
        "A rate-limited receipt retries once and stops after acceptance",
      );
      receiptSessionStatus = 503;
      const beforeSessionUnavailable = apiRequests.length;
      const beforeSessionReceipts = receipts.length;
      await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.equal(receipts.length, beforeSessionReceipts);
      assert.equal(
        apiRequests
          .slice(beforeSessionUnavailable)
          .filter((item) => item.path.endsWith("receipt-session")).length,
        4,
      );
      receiptSessionStatus = 200;

      await worker.evaluate(() => {
        self.__pushOriginalFetch = self.fetch;
        self.__pushFailedRequests = 0;
        self.fetch = async (url, options) => {
          if (url.endsWith("/receipts")) {
            self.__pushFailedRequests++;
            throw new TypeError("Fixture network failure");
          }
          return self.__pushOriginalFetch(url, options);
        };
      });
      const beforeNetworkFailure = receipts.length;
      const networkFailure = await dispatchWorkerEvent(worker, "notificationclick", validClick);
      assert.equal(networkFailure.navigations.at(-1), articleUrl);
      assert.equal(receipts.length, beforeNetworkFailure);
      assert.equal(await worker.evaluate(() => self.__pushFailedRequests), 4);
      await worker.evaluate(() => {
        self.fetch = self.__pushOriginalFetch;
      });

      await worker.evaluate(() => {
        self.__pushTimedOutRequests = 0;
        self.fetch = async (url, options) => {
          if (url.endsWith("/receipt-session")) {
            self.__pushTimedOutRequests++;
            return new Promise((_, reject) => {
              options.signal.addEventListener(
                "abort",
                () => reject(new DOMException("Fixture timeout", "AbortError")),
                { once: true },
              );
            });
          }
          return self.__pushOriginalFetch(url, options);
        };
      });
      const beforeTimeout = receipts.length;
      const timedOutAt = Date.now();
      const timedOutDisplay = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[8] },
      });
      assert.equal(
        timedOutDisplay.notifications.length,
        baselineNotifications.length + 3,
        "Telemetry timeout follows successful display",
      );
      assert.equal(receipts.length, beforeTimeout);
      assert.equal(await worker.evaluate(() => self.__pushTimedOutRequests), 2);
      assert.ok(
        Date.now() - timedOutAt < 6000,
        "An unresponsive receipt session has bounded worker lifetime",
      );
      await worker.evaluate(() => {
        self.fetch = self.__pushOriginalFetch;
      });

      await worker.evaluate(() => {
        self.__pushOriginalMatchAll = self.clients.matchAll;
        self.__pushOriginalOpenWindow = self.clients.openWindow;
        self.clients.matchAll = async () => [];
        self.clients.openWindow = async (url) => {
          self.__pushNavigations.push(url);
          return { url };
        };
      });
      const beforeOpenWindow = receipts.length;
      await dispatchWorkerEvent(worker, "notificationclick", validClick, true);
      assert.deepEqual(
        receipts.slice(beforeOpenWindow).map((item) => item.action),
        ["clicked", "opened"],
      );
      await worker.evaluate(() => {
        self.clients.openWindow = async () => null;
      });
      const beforeFailedOpen = receipts.length;
      await dispatchWorkerEvent(worker, "notificationclick", validClick, true);
      assert.deepEqual(
        receipts.slice(beforeFailedOpen).map((item) => item.action),
        ["clicked"],
        "A failed browser open is never counted as opened or read",
      );
      await worker.evaluate(() => {
        self.clients.matchAll = async () => [
          {
            url: `${self.location.origin}/settings/notifications`,
            navigate: async () => {
              throw new Error("Fixture navigation failure");
            },
            focus: async () => {},
          },
        ];
      });
      const beforeFailedNavigation = receipts.length;
      await dispatchWorkerEvent(worker, "notificationclick", validClick, true);
      assert.deepEqual(
        receipts.slice(beforeFailedNavigation).map((item) => item.action),
        ["clicked"],
      );
      await worker.evaluate(() => {
        self.clients.matchAll = self.__pushOriginalMatchAll;
        self.clients.openWindow = self.__pushOriginalOpenWindow;
      });

      let releaseReceiptGate;
      let heldClick;
      const gateStarted = new Promise((resolve) => {
        receiptGateStarted = resolve;
      });
      receiptGate = new Promise((resolve) => {
        releaseReceiptGate = resolve;
      });
      try {
        const beforeHeldReceipts = receipts.length;
        heldClick = dispatchWorkerEvent(worker, "notificationclick", validClick);
        await gateStarted;
        await Promise.race([
          writeWorkerConsent(page, null, null),
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error("Analytics blocked consent revocation")), 1000),
          ),
        ]);
        releaseReceiptGate();
        await heldClick;
        assert.equal(
          receipts.length,
          beforeHeldReceipts,
          "Revoked consent suppresses reporting after a delayed session response",
        );
      } finally {
        releaseReceiptGate();
        receiptGate = undefined;
        receiptGateStarted = undefined;
        await Promise.allSettled([heldClick]);
        await writeWorkerConsent(page, firstConsentId, firstUserId);
      }
      await worker.evaluate(
        ({ notifications, navigations }) => {
          self.__pushNotifications = notifications;
          self.__pushNavigations = navigations;
        },
        { notifications: baselineNotifications, navigations: baselineNavigations },
      );
      await checkClickRevocationOrder(page, worker, articleUrl);
      const maliciousClick = await dispatchWorkerEvent(worker, "notificationclick", {
        kind: "daily_must_read",
        url: "https://attacker.example/articles/untrusted",
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      });
      assert.deepEqual(
        maliciousClick.navigations,
        [articleUrl],
        "A notification cannot open an external destination",
      );
      await page.reload();
      await disable.waitFor();
      assert.deepEqual(
        await page.evaluate(() => window.__pushMetrics.permissions),
        [],
        "Returning users are not prompted again",
      );
      assert.equal(await page.evaluate(() => window.__pushMetrics.subscribes), 0);
      await page.setViewportSize({ width: 375, height: 812 });
      assert.equal(await disable.isVisible(), true);
      rejectDisable = true;
      await disable.click();
      await section.getByRole("alert").waitFor();
      await disable.waitFor();
      assert.equal(
        saved[0].enabled,
        true,
        "A failed disable request leaves server delivery enabled for an explicit retry",
      );
      rejectDisable = false;
      await disable.click();
      await enable.waitFor();
      assert.equal(saved[0].enabled, false);
      assert.equal(
        await page.evaluate(() => localStorage.getItem("fixture:push-subscription")),
        null,
      );
      const revoked = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[2] },
      });
      assert.equal(
        revoked.notifications.length,
        2,
        "Queued pushes cannot display after browser consent is disabled",
      );
      const revokedClick = await dispatchWorkerEvent(worker, "notificationclick", {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      });
      assert.deepEqual(
        revokedClick.navigations,
        [articleUrl],
        "A stale notification cannot open a previous account's article",
      );
      consentId = nextConsentId;
      await enable.click();
      await disable.waitFor();
      assert.equal(saved[0].id, subscriptionId, "Re-enabling reuses the same browser registration");
      assert.equal(saved[0].consent_id, nextConsentId);
      const replayed = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: { ...dailyPayload.data, notification_id: notificationIds[3] },
      });
      assert.equal(
        replayed.notifications.length,
        2,
        "Old consent cannot display queued alerts after re-enabling the same browser",
      );
      const oldConsentClick = await dispatchWorkerEvent(worker, "notificationclick", {
        kind: "daily_must_read",
        url: articleUrl,
        subscription_id: firstConsentId,
        notification_id: notificationIds[0],
      });
      assert.deepEqual(oldConsentClick.navigations, [articleUrl]);
      const newlyConsented = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: {
          ...dailyPayload.data,
          notification_id: notificationIds[3],
          subscription_id: nextConsentId,
        },
      });
      assert.equal(
        newlyConsented.notifications.length,
        3,
        "New consent can display the daily article on the same endpoint",
      );
      await disable.click();
      await enable.waitFor();

      const deniedContext = await browser.newContext({ timezoneId: "Asia/Kolkata" });
      await mockBrowserPush(deniedContext);
      const denied = await deniedContext.newPage();
      currentPage = denied;
      await denied.goto(`${origin}/settings/notifications`);
      const deniedSection = denied.getByRole("region", {
        name: "Daily must-read browser notifications",
      });
      await deniedSection
        .getByRole("button", { name: "Enable notifications on this browser", exact: true })
        .waitFor();
      const priorWrites = writes.length;
      await denied.evaluate(() => {
        window.__pushPermissionResult = "denied";
      });
      await deniedSection
        .getByRole("button", { name: "Enable notifications on this browser", exact: true })
        .click();
      await deniedSection
        .getByText(/blocked|permission|allow notifications/i)
        .last()
        .waitFor();
      assert.equal(await denied.evaluate(() => Notification.permission), "denied");
      assert.equal(await denied.evaluate(() => window.__pushMetrics.subscribes), 0);
      assert.equal(writes.length, priorWrites, "Declining permission does not save a subscription");
      await denied.reload();
      await deniedSection
        .getByRole("heading", { name: "Browser notifications", exact: true })
        .waitFor();
      assert.deepEqual(
        await denied.evaluate(() => window.__pushMetrics.permissions),
        [],
        "Denied permissions are not repeatedly prompted",
      );
      await deniedContext.close();

      rejectRegistration = true;
      const failingContext = await browser.newContext({ timezoneId: "Asia/Kolkata" });
      await mockBrowserPush(failingContext);
      const failing = await failingContext.newPage();
      currentPage = failing;
      await failing.goto(`${origin}/settings/notifications`);
      const failingSection = failing.getByRole("region", {
        name: "Daily must-read browser notifications",
      });
      await failingSection
        .getByRole("button", { name: "Enable notifications on this browser", exact: true })
        .click();
      await failingSection.getByRole("alert").waitFor();
      await failingSection
        .getByRole("button", { name: "Enable notifications on this browser", exact: true })
        .waitFor();
      assert.equal(
        await failing.evaluate(() => localStorage.getItem("fixture:push-subscription")),
        null,
        "Failed server enrollment removes the new browser subscription",
      );
      assert.equal(saved[0].enabled, false);
      await failingContext.close();

      const iphoneContext = await browser.newContext({
        userAgent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
      });
      await mockBrowserPush(iphoneContext);
      const iphone = await iphoneContext.newPage();
      currentPage = iphone;
      await iphone.goto(`${origin}/settings/notifications`);
      const iphoneSection = iphone.getByRole("region", {
        name: "Daily must-read browser notifications",
      });
      await iphoneSection.getByText(/add DevFeed to your Home Screen/).waitFor();
      assert.equal(await iphoneSection.getByRole("button").count(), 0);
      assert.deepEqual(await iphone.evaluate(() => window.__pushMetrics.permissions), []);
      await iphoneContext.close();

      rejectRegistration = false;
      consentId = expiringConsentId;
      currentPage = page;
      await enable.click();
      await disable.waitFor();
      authenticated = false;
      await page.reload();
      await page.getByRole("heading", { name: "Make this feed yours", exact: true }).waitFor();
      await page.waitForFunction(() => localStorage.getItem("fixture:push-subscription") === null);
      const expiredSession = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: {
          ...dailyPayload.data,
          notification_id: notificationIds[4],
          subscription_id: expiringConsentId,
        },
      });
      assert.equal(
        expiredSession.notifications.length,
        3,
        "An expired session returned as HTTP 200 null clears local consent before a queued daily alert can display",
      );

      authenticated = true;
      consentId = switchingConsentId;
      await page.goto(`${origin}/settings/notifications`);
      await enable.click();
      await disable.waitFor();
      assert.equal(
        await page.evaluate(() => localStorage.getItem("fixture:push-subscription")),
        "true",
      );
      currentUserId = secondUserId;
      await page.goto(`${origin}/`);
      await page.waitForFunction(() => localStorage.getItem("fixture:push-subscription") === null);
      const switchedAccount = await dispatchWorkerEvent(worker, "push", {
        ...dailyPayload,
        data: {
          ...dailyPayload.data,
          notification_id: notificationIds[5],
          subscription_id: switchingConsentId,
        },
      });
      assert.equal(
        switchedAccount.notifications.length,
        3,
        "Signing into a different account on the homepage clears old consent without a notification settings visit",
      );
      await context.close();
      assert.deepEqual(errors, []);
    } catch (error) {
      const content = await currentPage
        ?.getByRole("region", { name: "Daily must-read browser notifications" })
        .innerText({ timeout: 1000 })
        .catch(() => "Region unavailable");
      const metrics = await currentPage
        ?.evaluate(() => window.__pushMetrics)
        .catch(() => undefined);
      throw new Error(
        `${error.message}\n${content}\n${JSON.stringify({ metrics, writes, errors })}\n${logs}`,
        { cause: error },
      );
    } finally {
      await browser?.close();
      app?.kill("SIGTERM");
      fixture.closeAllConnections();
      await new Promise((resolve) => fixture.close(resolve));
    }
  },
);
