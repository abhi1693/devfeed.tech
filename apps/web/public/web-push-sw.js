/* DevFeed's notification worker deliberately has no fetch handler or offline cache. */
const databaseName = "devfeed-browser-push";
let pending = Promise.resolve();

function serial(operation) {
  const next = pending.then(operation);
  pending = next.catch(() => {});
  return next;
}

function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("consent");
      request.result.createObjectStore("receipts", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("Browser notification storage failed"));
  });
}

async function changeConsent(subscriptionId, accountId) {
  const db = await database();
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction("consent", "readwrite");
      const store = transaction.objectStore("consent");
      if (subscriptionId) {
        store.put(subscriptionId, "subscription");
        store.put(accountId, "account");
      } else {
        store.delete("subscription");
        store.delete("account");
      }
      transaction.oncomplete = resolve;
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction failed"));
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction aborted"));
    });
    if (!subscriptionId) {
      const notifications = await self.registration.getNotifications();
      notifications.forEach((notification) => notification.close());
    }
  } finally {
    db.close();
  }
}

async function reconcileAccount(accountId) {
  const db = await database();
  let revoked = false;
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction("consent", "readwrite");
      const store = transaction.objectStore("consent");
      const owner = store.get("account");
      const consent = store.get("subscription");
      consent.onsuccess = () => {
        if (!consent.result || owner.result === accountId) return;
        store.delete("subscription");
        store.delete("account");
        revoked = true;
      };
      transaction.oncomplete = resolve;
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction failed"));
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction aborted"));
    });
    if (revoked) {
      const notifications = await self.registration.getNotifications();
      notifications.forEach((notification) => notification.close());
    }
    return revoked;
  } finally {
    db.close();
  }
}

// Persist consent and reserve the receipt in one transaction. Concurrent deliveries
// cannot both display the same notification, including after a worker restart.
async function reserveReceipt(subscriptionId, notificationId) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(["consent", "receipts"], "readwrite");
      const consent = transaction.objectStore("consent").get("subscription");
      const receipts = transaction.objectStore("receipts");
      let reserved = false;
      consent.onsuccess = () => {
        if (consent.result !== subscriptionId) return;
        const id = `${subscriptionId}:${notificationId}`;
        const existing = receipts.get(id);
        existing.onsuccess = () => {
          if (existing.result) return;
          receipts.put({ id, created: Date.now() });
          reserved = true;
          const cursor = receipts.openCursor();
          cursor.onsuccess = () => {
            const item = cursor.result;
            if (!item) return;
            if (item.value.created < Date.now() - 90 * 86400000) item.delete();
            item.continue();
          };
        };
      };
      transaction.oncomplete = () => resolve(reserved);
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction failed"));
      transaction.onabort = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction aborted"));
    });
  } finally {
    db.close();
  }
}

async function releaseReceipt(subscriptionId, notificationId) {
  const db = await database();
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction("receipts", "readwrite");
      transaction.objectStore("receipts").delete(`${subscriptionId}:${notificationId}`);
      transaction.oncomplete = resolve;
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction failed"));
    });
  } finally {
    db.close();
  }
}

async function currentConsent() {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction("consent");
      const store = transaction.objectStore("consent");
      const subscription = store.get("subscription");
      const account = store.get("account");
      transaction.oncomplete = () =>
        resolve({ subscriptionId: subscription.result, accountId: account.result });
      transaction.onerror = () =>
        reject(transaction.error ?? new Error("Browser notification storage transaction failed"));
    });
  } finally {
    db.close();
  }
}

const receiptActions = new Set(["displayed", "clicked", "opened"]);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function receiptRequest(path, options, readJson = false) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    const data = readJson && response.ok ? await response.json() : null;
    return { ok: response.ok, status: response.status, data };
  } finally {
    clearTimeout(timeout);
  }
}

async function authorizedPush(subscriptionId, notificationId) {
  try {
    const query = new URLSearchParams({
      subscription_id: subscriptionId,
      notification_id: notificationId,
    });
    const response = await receiptRequest(
      `/api/v1/user/notifications/push/authorization?${query}`,
      { method: "GET" },
      true,
    );
    return response.ok && response.data?.authorized === true;
  } catch {
    // A relay may have accepted this before sign-out in another browser surface.
    // Unavailable authentication suppresses this delivery without revoking consent.
    return false;
  }
}

// Feedback never holds the consent/navigation queue or changes its state. The
// short-lived CSRF token stays in memory and is obtained without renewing login.
async function reportReceipt(subscriptionId, notificationId, action) {
  if (!uuid.test(subscriptionId) || !uuid.test(notificationId) || !receiptActions.has(action))
    return;
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      let retry = false;
      try {
        const consent = await currentConsent();
        if (consent.subscriptionId !== subscriptionId || !consent.accountId) return;
        const session = await receiptRequest(
          "/api/v1/user/notifications/push/receipt-session",
          { method: "GET" },
          true,
        );
        if (!session.ok) {
          retry = session.status === 429 || session.status >= 500;
        } else {
          if (
            session.data?.user_id !== consent.accountId ||
            typeof session.data.csrf_token !== "string" ||
            !session.data.csrf_token
          )
            return;
          const latest = await currentConsent();
          if (latest.subscriptionId !== subscriptionId || latest.accountId !== consent.accountId)
            return;
          const receipt = await receiptRequest("/api/v1/user/notifications/push/receipts", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-csrf-token": session.data.csrf_token,
            },
            body: JSON.stringify({
              notification_id: notificationId,
              subscription_id: subscriptionId,
              action,
            }),
          });
          if (receipt.ok) return;
          retry = receipt.status === 429 || receipt.status >= 500;
        }
      } catch {
        retry = true;
      }
      if (!retry || attempt === 1) return;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  } catch {
    // IndexedDB or network failure must never prevent display or navigation.
  }
}

function articleUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, self.location.origin);
    if (
      url.origin !== self.location.origin ||
      !/^\/articles\/[^/]+$/.test(url.pathname) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}

// Additional types require their own action policy and explicit consent flow.
// The daily Must Read remains the only active receiver.
const pushTypes = new Map([["daily_must_read", { actionUrl: articleUrl }]]);

self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("message", (event) => {
  if (
    event.origin !== self.location.origin ||
    !["devfeed:push-consent", "devfeed:push-account"].includes(event.data?.type) ||
    !event.source?.url ||
    new URL(event.source.url).origin !== self.location.origin
  )
    return;
  const accountId = event.data.account_id;
  if (event.data.type === "devfeed:push-account") {
    if (typeof accountId !== "string" || !accountId) return;
    event.waitUntil(
      serial(() => reconcileAccount(accountId))
        .then((revoked) => event.ports[0]?.postMessage({ ok: true, revoked }))
        .catch(() => event.ports[0]?.postMessage({ ok: false })),
    );
    return;
  }
  const subscriptionId = event.data.subscription_id;
  if (subscriptionId !== null && typeof subscriptionId !== "string") return;
  if (subscriptionId && (typeof accountId !== "string" || !accountId)) return;
  event.waitUntil(
    serial(() => changeConsent(subscriptionId, accountId))
      .then(() => event.ports[0]?.postMessage({ ok: true }))
      .catch(() => event.ports[0]?.postMessage({ ok: false })),
  );
});

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let payload;
      try {
        payload = event.data?.json();
      } catch {
        return;
      }
      const { notification, data } = payload ?? {};
      const handler = pushTypes.get(data?.kind);
      const url = handler?.actionUrl(data?.url);
      if (
        !handler ||
        !uuid.test(data.subscription_id) ||
        !uuid.test(data.notification_id) ||
        typeof data.expires_at !== "number" ||
        !Number.isFinite(data.expires_at) ||
        data.expires_at <= Date.now() / 1000 ||
        !url ||
        typeof notification?.title !== "string" ||
        !notification.title ||
        (notification.navigate && handler.actionUrl(notification.navigate) !== url)
      )
        return;
      const consent = await currentConsent();
      if (consent.subscriptionId !== data.subscription_id || !consent.accountId) return;
      // Keep the network request outside the queue so sign-out can revoke while
      // authorization is pending. The final queued checks use the latest consent.
      if (!(await authorizedPush(data.subscription_id, data.notification_id))) return;
      return serial(async () => {
        const latest = await currentConsent();
        if (
          latest.subscriptionId !== data.subscription_id ||
          latest.accountId !== consent.accountId ||
          data.expires_at <= Date.now() / 1000
        )
          return;
        if (!(await reserveReceipt(data.subscription_id, data.notification_id))) return;
        try {
          if (data.expires_at <= Date.now() / 1000) {
            await releaseReceipt(data.subscription_id, data.notification_id);
            return;
          }
          await self.registration.showNotification(notification.title, {
            body:
              typeof notification.body === "string"
                ? notification.body
                : "Your must-read for today.",
            tag: `push-${data.notification_id}`,
            renotify: false,
            data: {
              kind: data.kind,
              url,
              subscription_id: data.subscription_id,
              notification_id: data.notification_id,
            },
          });
          return data;
        } catch (error) {
          await releaseReceipt(data.subscription_id, data.notification_id);
          throw error;
        }
      });
    })().then((data) =>
      data ? reportReceipt(data.subscription_id, data.notification_id, "displayed") : undefined,
    ),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const handler = pushTypes.get(event.notification.data?.kind);
  const url = handler?.actionUrl(event.notification.data?.url);
  if (!url) return;
  event.waitUntil(
    serial(async () => {
      const data = event.notification.data;
      const consent = await currentConsent();
      if (!consent.subscriptionId || consent.subscriptionId !== data.subscription_id) return;
      let opened = false;
      try {
        const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
        for (const client of clients) {
          if (new URL(client.url).origin !== self.location.origin) continue;
          if (!("navigate" in client) || !("focus" in client)) continue;
          opened = Boolean(await client.navigate(url));
          if (opened) await client.focus().catch(() => {});
          return { ...data, opened };
        }
        opened = Boolean(await self.clients.openWindow(url));
      } catch {
        // A validated click is still counted when the browser cannot open it.
      }
      return { ...data, opened };
    }).then(async (data) => {
      if (!data) return;
      await reportReceipt(data.subscription_id, data.notification_id, "clicked");
      if (data.opened) await reportReceipt(data.subscription_id, data.notification_id, "opened");
    }),
  );
});
