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
    request.onerror = () => reject(request.error);
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
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
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
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
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
// cannot both display the same daily article, including after a worker restart.
async function reserveReceipt(subscriptionId, dayId) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(["consent", "receipts"], "readwrite");
      const consent = transaction.objectStore("consent").get("subscription");
      const receipts = transaction.objectStore("receipts");
      let reserved = false;
      consent.onsuccess = () => {
        if (consent.result !== subscriptionId) return;
        const id = `${subscriptionId}:${dayId}`;
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
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}

async function releaseReceipt(subscriptionId, dayId) {
  const db = await database();
  try {
    await new Promise((resolve, reject) => {
      const transaction = db.transaction("receipts", "readwrite");
      transaction.objectStore("receipts").delete(`${subscriptionId}:${dayId}`);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    db.close();
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

self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("message", (event) => {
  if (
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
    serial(async () => {
      let payload;
      try {
        payload = event.data?.json();
      } catch {
        return;
      }
      const { notification, data } = payload ?? {};
      const url = articleUrl(data?.url);
      if (
        data?.kind !== "daily_must_read" ||
        typeof data.subscription_id !== "string" ||
        typeof data.day_id !== "string" ||
        !data.day_id ||
        typeof data.expires_at !== "number" ||
        !Number.isFinite(data.expires_at) ||
        data.expires_at <= Date.now() / 1000 ||
        !url ||
        typeof notification?.title !== "string" ||
        !notification.title ||
        (notification.navigate && articleUrl(notification.navigate) !== url)
      )
        return;
      if (!(await reserveReceipt(data.subscription_id, data.day_id))) return;
      try {
        if (data.expires_at <= Date.now() / 1000) return;
        await self.registration.showNotification(notification.title, {
          body:
            typeof notification.body === "string" ? notification.body : "Your must-read for today.",
          tag: `mustread-${data.day_id}`,
          renotify: false,
          data: { url, subscription_id: data.subscription_id },
        });
      } catch (error) {
        await releaseReceipt(data.subscription_id, data.day_id);
        throw error;
      }
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = articleUrl(event.notification.data?.url);
  if (!url) return;
  event.waitUntil(
    (async () => {
      const db = await database();
      const consent = await new Promise((resolve, reject) => {
        const request = db.transaction("consent").objectStore("consent").get("subscription");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      }).finally(() => db.close());
      if (!consent || consent !== event.notification.data.subscription_id) return;
      const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of clients) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        if (!("navigate" in client) || !("focus" in client)) continue;
        await client.navigate(url);
        await client.focus();
        return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
