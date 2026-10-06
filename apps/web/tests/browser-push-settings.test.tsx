// @vitest-environment jsdom
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserPushSettings } from "@/components/browser-push-settings";
import {
  browserPushAvailability,
  clearBrowserPush,
  pushEndpointHash,
  reconcileBrowserPushAccount,
  setBrowserPushConsent,
} from "@/lib/browser-push";

const state = vi.hoisted(() => ({
  extension: false,
  deliveryTimezone: null as string | null,
  request: vi.fn(),
  user: { user_id: "alice", csrf_token: "csrf" },
}));
vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: state.user }),
}));
vi.mock("@/lib/user", () => ({ userRequest: state.request }));
vi.mock("@/lib/reader-runtime", () => ({
  readerIsExtension: () => state.extension,
  readerWebsiteLink: (href: string) =>
    state.extension
      ? { href: `https://devfeed.tech${href}`, target: "_blank", rel: "noopener noreferrer" }
      : { href },
}));

const endpoint = "https://fcm.googleapis.com/fcm/send/fixture-daily";
let permission: NotificationPermission;
let existing: PushSubscription | null;
let registration: ServiceWorkerRegistration;
let subscription: PushSubscription;
let requestPermission: ReturnType<typeof vi.fn>;
let register: ReturnType<typeof vi.fn>;
let unsubscribe: ReturnType<typeof vi.fn>;
let subscriptions: {
  id: string;
  consent_id: string;
  enabled: boolean;
  endpoint_hash: string;
  timezone: string;
}[];
let postFailure: boolean;
let deleteFailure: boolean;
let postResolve: ((value: unknown) => void) | undefined;
let deferPost: boolean;
let consent: string | null;
let owner: string | null;
const initialServiceWorker = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");

beforeEach(() => {
  permission = "default";
  existing = null;
  subscriptions = [];
  postFailure = false;
  deleteFailure = false;
  postResolve = undefined;
  deferPost = false;
  consent = null;
  owner = null;
  state.extension = false;
  state.deliveryTimezone = null;
  state.request.mockReset();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal(
    "MessageChannel",
    class {
      port1 = { onmessage: null as ((event: { data: unknown }) => void) | null, close() {} };
      port2 = { ack: (data = { ok: true }) => this.port1.onmessage?.({ data }) };
    },
  );
  requestPermission = vi.fn(async () => {
    permission = "granted";
    return permission;
  });
  vi.stubGlobal("Notification", {
    get permission() {
      return permission;
    },
    requestPermission,
  });
  unsubscribe = vi.fn(async () => {
    existing = null;
    return true;
  });
  subscription = {
    endpoint,
    options: {},
    toJSON: () => ({ endpoint, keys: { auth: "auth", p256dh: "key" } }),
    unsubscribe,
  } as unknown as PushSubscription;
  registration = {
    active: {
      scriptURL: new URL("/web-push-sw.js", location.origin).href,
      postMessage: (
        data: { type: string; subscription_id?: string | null; account_id?: string | null },
        ports: [{ ack: (data?: { ok: boolean; revoked?: boolean }) => void }],
      ) => {
        if (data.type === "devfeed:push-account") {
          const revoked = Boolean(consent && owner !== data.account_id);
          if (revoked) {
            consent = null;
            owner = null;
          }
          ports[0].ack({ ok: true, revoked });
        } else {
          consent = data.subscription_id ?? null;
          owner = consent ? (data.account_id ?? null) : null;
          ports[0].ack();
        }
      },
    },
    pushManager: {
      getSubscription: vi.fn(async () => existing),
      subscribe: vi.fn(async () => {
        existing = subscription;
        return subscription;
      }),
    },
  } as unknown as ServiceWorkerRegistration;
  register = vi.fn(async () => registration);
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { getRegistration: vi.fn(async () => (existing ? registration : undefined)), register },
  });
  state.request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "notifications/push/config")
      return {
        enabled: true,
        public_key: "BA",
        delivery_hour: 9,
        delivery_timezone: state.deliveryTimezone,
      };
    if (init?.method === "DELETE") {
      if (deleteFailure) throw new Error("Offline");
      subscriptions = [];
      return undefined;
    }
    if (init?.method === "POST") {
      if (postFailure) throw new Error("Offline");
      const saved = {
        id: "device",
        consent_id: "device-consent",
        enabled: true,
        timezone: "Europe/London",
      };
      if (deferPost)
        return new Promise((resolve) => {
          postResolve = resolve;
        });
      return saved;
    }
    return { subscriptions };
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  if (initialServiceWorker) Object.defineProperty(navigator, "serviceWorker", initialServiceWorker);
  else Reflect.deleteProperty(navigator, "serviceWorker");
});

it("asks for permission only on a click and enables only after authenticated persistence", async () => {
  const user = userEvent.setup();
  deferPost = true;
  render(<BrowserPushSettings />);
  const enable = await screen.findByRole("button", {
    name: "Enable notifications on this browser",
  });
  expect(requestPermission).not.toHaveBeenCalled();
  expect(register).not.toHaveBeenCalled();
  await user.click(enable);
  await waitFor(() => expect(postResolve).toBeTypeOf("function"));
  expect(requestPermission).toHaveBeenCalledOnce();
  expect(consent).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Disable notifications on this browser" }),
  ).toBeNull();
  const write = state.request.mock.calls.find(([, init]) => init?.method === "POST");
  expect(write?.[1].headers["X-CSRF-Token"]).toBe("csrf");
  expect(JSON.parse(write?.[1].body)).toMatchObject({
    endpoint,
    keys: { auth: "auth", p256dh: "key" },
    timezone: expect.any(String),
  });
  postResolve!({
    id: "device",
    consent_id: "device-consent",
    enabled: true,
    timezone: "Europe/London",
  });
  await screen.findByRole("button", { name: "Disable notifications on this browser" });
  expect(consent).toBe("device-consent");
  expect(owner).toBe("alice");
  expect(register).toHaveBeenCalledWith("/web-push-sw.js", { scope: "/", updateViaCache: "none" });
});

it("rolls back a browser subscription when the server cannot save consent", async () => {
  postFailure = true;
  const user = userEvent.setup();
  render(<BrowserPushSettings />);
  await user.click(
    await screen.findByRole("button", { name: "Enable notifications on this browser" }),
  );
  await screen.findByRole("alert");
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(consent).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Disable notifications on this browser" }),
  ).toBeNull();
});

it("keeps saved enrollment available for retry when disabling fails, then removes it with CSRF", async () => {
  permission = "granted";
  existing = subscription;
  subscriptions = [
    {
      id: "device",
      consent_id: "device-consent",
      enabled: true,
      endpoint_hash: await pushEndpointHash(endpoint),
      timezone: "Europe/London",
    },
  ];
  deleteFailure = true;
  const user = userEvent.setup();
  render(<BrowserPushSettings />);
  await user.click(
    await screen.findByRole("button", { name: "Disable notifications on this browser" }),
  );
  await screen.findByRole("alert");
  expect(unsubscribe).not.toHaveBeenCalled();
  expect(consent).toBe("device-consent");
  deleteFailure = false;
  await user.click(screen.getByRole("button", { name: "Disable notifications on this browser" }));
  await screen.findByText("Daily must-read notifications are disabled on this browser.");
  expect(consent).toBeNull();
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(state.request).toHaveBeenCalledWith("notifications/push/subscriptions/device", {
    method: "DELETE",
    headers: { "X-CSRF-Token": "csrf" },
  });
});

it("does not enroll after a denied permission prompt", async () => {
  requestPermission.mockImplementation(async () => {
    permission = "denied";
    return permission;
  });
  const user = userEvent.setup();
  render(<BrowserPushSettings />);
  await user.click(
    await screen.findByRole("button", { name: "Enable notifications on this browser" }),
  );
  await screen.findByText(/Notifications are blocked.*reload this page/);
  expect(register).not.toHaveBeenCalled();
  expect(state.request.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("reports a browser policy error if requesting permission throws", async () => {
  requestPermission.mockImplementation(() => {
    throw new Error("Blocked by browser policy");
  });
  const user = userEvent.setup();
  render(<BrowserPushSettings />);
  await user.click(
    await screen.findByRole("button", { name: "Enable notifications on this browser" }),
  );
  await screen.findByRole("alert");
  expect(register).not.toHaveBeenCalled();
  expect(state.request.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("routes both bundled readers to website-origin enrollment without browser API calls", async () => {
  state.extension = true;
  render(<BrowserPushSettings />);
  const link = await screen.findByRole("link", { name: "Manage browser notifications" });
  expect(link.getAttribute("href")).toBe("https://devfeed.tech/settings/notifications");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(state.request).not.toHaveBeenCalled();
  expect(requestPermission).not.toHaveBeenCalled();
});

it("shows the account's delivery timezone when another browser established the schedule", async () => {
  state.deliveryTimezone = "America/New_York";
  render(<BrowserPushSettings />);
  await screen.findByRole("button", { name: "Enable notifications on this browser" });
  expect(screen.getByText(/Delivered around.*America\/New_York/)).toBeTruthy();
});

it("gives iPhone users a Home Screen enrollment instruction", async () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("iPhone");
  render(<BrowserPushSettings />);
  await screen.findByText(/add DevFeed to your Home Screen/);
  expect(browserPushAvailability()).toBe("home-screen");
  expect(state.request).not.toHaveBeenCalled();
});

it("clears persistent consent before unsubscribing during sign-out", async () => {
  existing = subscription;
  consent = "device";
  unsubscribe.mockImplementation(async () => {
    expect(consent).toBeNull();
    return true;
  });
  await clearBrowserPush();
  expect(unsubscribe).toHaveBeenCalledOnce();
});

it("preserves the same account's consent and revokes it on an account switch", async () => {
  existing = subscription;
  await setBrowserPushConsent(registration, "device-consent", "alice");
  await reconcileBrowserPushAccount("alice");
  expect(consent).toBe("device-consent");
  expect(unsubscribe).not.toHaveBeenCalled();
  await reconcileBrowserPushAccount("bob");
  expect(consent).toBeNull();
  expect(owner).toBeNull();
  expect(unsubscribe).toHaveBeenCalledOnce();
});
