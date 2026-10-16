import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";

const origin = "https://devfeed.test";
const subscriptionId = "22222222-2222-4222-8222-222222222222";
const accountId = "11111111-1111-4111-8111-111111111111";
const notificationId = "33333333-3333-4333-8333-333333333333";
let handlers: Map<string, (event: Record<string, unknown>) => void>;
let worker: ReturnType<typeof makeWorker>;
let request: ReturnType<typeof vi.fn<typeof fetch>>;

function makeWorker() {
  return {
    location: { origin },
    addEventListener: (name: string, handler: (event: Record<string, unknown>) => void) =>
      handlers.set(name, handler),
    skipWaiting: vi.fn().mockResolvedValue(undefined),
    clients: {
      claim: vi.fn().mockResolvedValue(undefined),
      matchAll: vi.fn().mockResolvedValue([]),
      openWindow: vi.fn().mockResolvedValue({}),
    },
    registration: {
      getNotifications: vi.fn().mockResolvedValue([]),
      showNotification: vi.fn().mockResolvedValue(undefined),
    },
  };
}

async function dispatch(name: string, properties: Record<string, unknown> = {}) {
  const pending: Promise<unknown>[] = [];
  handlers.get(name)!({
    ...properties,
    waitUntil: (promise: Promise<unknown>) => pending.push(promise),
  });
  await Promise.all(pending);
}

async function consent(subscription: string | null = subscriptionId, account = accountId) {
  const reply = vi.fn();
  await dispatch("message", {
    origin,
    source: { url: `${origin}/settings/notifications` },
    data: { type: "devfeed:push-consent", subscription_id: subscription, account_id: account },
    ports: [{ postMessage: reply }],
  });
  expect(reply).toHaveBeenCalledWith({ ok: true });
}

function payload() {
  return {
    notification: { title: "Your daily must-read", navigate: `${origin}/articles/daily` },
    data: {
      kind: "daily_must_read",
      subscription_id: subscriptionId,
      notification_id: notificationId,
      url: `${origin}/articles/daily`,
      expires_at: Date.now() / 1000 + 3600,
    },
  };
}

async function push(value = payload()) {
  await dispatch("push", { data: { json: () => value } });
}

beforeEach(async () => {
  vi.resetModules();
  handlers = new Map();
  worker = makeWorker();
  request = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("/authorization?")) return Response.json({ authorized: true });
    if (path.endsWith("/receipt-session"))
      return Response.json({ user_id: accountId, csrf_token: "csrf" });
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("self", worker);
  vi.stubGlobal("fetch", request);
  // The deployed classic service worker is executed as-is, including its event handlers.
  // @ts-expect-error The public worker is JavaScript without a module declaration.
  await import("../public/web-push-sw.js");
});

afterEach(() => vi.unstubAllGlobals());

it("takes control on installation and activation without registering an offline fetch handler", async () => {
  await dispatch("install");
  await dispatch("activate");
  expect(worker.skipWaiting).toHaveBeenCalledOnce();
  expect(worker.clients.claim).toHaveBeenCalledOnce();
  expect(handlers.has("fetch")).toBe(false);
});

it("displays an authorized delivery once and sends a CSRF-protected receipt", async () => {
  await consent();
  await Promise.all([push(), push()]);
  expect(worker.registration.showNotification).toHaveBeenCalledOnce();
  expect(worker.registration.showNotification).toHaveBeenCalledWith(
    "Your daily must-read",
    expect.objectContaining({
      body: "Your must-read for today.",
      renotify: false,
      data: expect.objectContaining({ notification_id: notificationId }),
    }),
  );
  expect(request).toHaveBeenCalledWith(
    "/api/v1/user/notifications/push/receipts",
    expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      headers: { "Content-Type": "application/json", "x-csrf-token": "csrf" },
      body: JSON.stringify({
        notification_id: notificationId,
        subscription_id: subscriptionId,
        action: "displayed",
      }),
    }),
  );
});

it("suppresses deliveries without consent and after revocation, closing existing notifications", async () => {
  await push();
  await consent();
  const close = vi.fn();
  worker.registration.getNotifications.mockResolvedValue([{ close }]);
  await consent(null);
  await push();
  expect(close).toHaveBeenCalledOnce();
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
});

it("revokes consent when the active account changes but preserves same-account consent", async () => {
  await consent();
  const reply = vi.fn();
  const event = {
    origin,
    source: { url: origin },
    ports: [{ postMessage: reply }],
    data: { type: "devfeed:push-account", account_id: accountId },
  };
  await dispatch("message", event);
  expect(reply).toHaveBeenLastCalledWith({ ok: true, revoked: false });
  await dispatch("message", { ...event, data: { ...event.data, account_id: "another-reader" } });
  expect(reply).toHaveBeenLastCalledWith({ ok: true, revoked: true });
  await push();
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
});

it.each([
  "https://evil.test/articles/daily",
  "/articles/daily?secret=1",
  "/articles/daily#fragment",
  "/settings/profile",
  "http://[",
])("rejects an unsafe notification URL: %s", async (url) => {
  await consent();
  const value = payload();
  value.data.url = url;
  await push(value);
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
});

it("rejects malformed JSON, expired messages, unknown types and mismatched navigation", async () => {
  await consent();
  await dispatch("push", {
    data: {
      json: () => {
        throw new Error("Invalid JSON");
      },
    },
  });
  for (const field of [
    { kind: "other" },
    { expires_at: 0 },
    { subscription_id: "invalid" },
    { notification_id: "invalid" },
  ]) {
    const value = payload();
    Object.assign(value.data, field);
    await push(value);
  }
  const value = payload();
  value.notification.navigate = `${origin}/articles/another`;
  await push(value);
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
});

it("fails closed when authorization is denied or unavailable", async () => {
  await consent();
  request.mockResolvedValueOnce(new Response(null, { status: 401 }));
  await push();
  request.mockRejectedValueOnce(new Error("Offline"));
  await push();
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
});

it("releases the reservation after display failure so a redelivery can succeed", async () => {
  await consent();
  worker.registration.showNotification.mockRejectedValueOnce(new Error("Notification unavailable"));
  await expect(push()).rejects.toThrow("Notification unavailable");
  await push();
  expect(worker.registration.showNotification).toHaveBeenCalledTimes(2);
});

it("ignores consent messages from another origin or an untrusted source", async () => {
  for (const properties of [
    { origin: "https://evil.test", source: { url: origin } },
    { origin, source: { url: "https://evil.test" } },
  ]) {
    await dispatch("message", {
      ...properties,
      data: {
        type: "devfeed:push-consent",
        subscription_id: subscriptionId,
        account_id: accountId,
      },
      ports: [],
    });
  }
  await push();
  expect(worker.registration.showNotification).not.toHaveBeenCalled();
});

it("focuses an existing same-origin client on a validated click and reports both actions", async () => {
  await consent();
  const focus = vi.fn().mockResolvedValue(undefined);
  const navigate = vi.fn().mockResolvedValue({});
  worker.clients.matchAll.mockResolvedValue([
    { url: "https://other.test/" },
    { url: origin, navigate, focus },
  ]);
  const close = vi.fn();
  await dispatch("notificationclick", { notification: { close, data: payload().data } });
  expect(close).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledWith(`${origin}/articles/daily`);
  expect(focus).toHaveBeenCalledOnce();
  const bodies = request.mock.calls
    .filter(([url]) => String(url).endsWith("/receipts"))
    .map(([, options]) => JSON.parse(options!.body as string).action);
  expect(bodies).toEqual(["clicked", "opened"]);
});

it("opens a new client when none can navigate, and ignores clicks after sign-out", async () => {
  await consent();
  await dispatch("notificationclick", { notification: { close: vi.fn(), data: payload().data } });
  expect(worker.clients.openWindow).toHaveBeenCalledWith(`${origin}/articles/daily`);
  worker.clients.openWindow.mockClear();
  await consent(null);
  await dispatch("notificationclick", { notification: { close: vi.fn(), data: payload().data } });
  expect(worker.clients.openWindow).not.toHaveBeenCalled();
});
