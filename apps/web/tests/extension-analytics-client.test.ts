// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { startExtensionAnalytics } from "../../extensions/src/analytics";
import { analyticsEventName } from "@/lib/analytics";
let stop = () => {};
afterEach(() => {
  stop();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.location.hash = "";
});
function setup(enabled = true) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: { request: async (_name: string, callback: () => unknown) => callback() },
  });
  const network = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "POST" ? new Response(null, { status: 204 }) : Response.json({ enabled }),
  );
  stop = startExtensionAnalytics(network);
  return network;
}
const emit = (detail: unknown) =>
  window.dispatchEvent(new CustomEvent(analyticsEventName, { detail }));
it("does not create identifiers or send events when server tracking is disabled", async () => {
  const network = setup(false);
  await vi.waitFor(() => expect(network).toHaveBeenCalledTimes(1));
  emit({ name: "article_like", params: { article_id: "11111111-1111-4111-8111-111111111111" } });
  await Promise.resolve();
  expect(network).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0);
});
it("normalizes page views and excludes raw search text, cookies and unsupported events", async () => {
  window.location.hash = "#/search?q=private@example.test";
  const network = setup();
  await vi.waitFor(() => expect(network).toHaveBeenCalledTimes(2));
  const init = network.mock.calls[1][1]!;
  const payload = JSON.parse(String(init.body));
  expect(payload.client_id).toMatch(/^\d+\.\d+$/);
  expect(payload.event).toEqual({ name: "page_view", params: { page_path: "/search" } });
  expect(payload.extension_surface).toBe("newtab");
  expect(payload.locale).toBeTruthy();
  expect(payload.timezone).toBeTruthy();
  expect(payload.viewport_width).toBeGreaterThanOrEqual(0);
  expect(payload.viewport_height).toBeGreaterThanOrEqual(0);
  expect(init.credentials).toBe("omit");
  expect(JSON.stringify(payload)).not.toContain("private@example");
  emit({
    name: "article_like",
    params: { article_id: "11111111-1111-4111-8111-111111111111", email: "private" },
  });
  await Promise.resolve();
  expect(network).toHaveBeenCalledTimes(2);
  emit({ name: "article_like", params: { article_id: "11111111-1111-4111-8111-111111111111" } });
  await vi.waitFor(() => expect(network).toHaveBeenCalledTimes(3));
  expect(JSON.parse(String(network.mock.calls[2][1]?.body)).session_id).toBe(payload.session_id);
});
it("keeps the installation identity across starts but rotates an inactive session", async () => {
  const network = setup();
  await vi.waitFor(() => expect(network).toHaveBeenCalledTimes(2));
  const first = JSON.parse(String(network.mock.calls[1][1]?.body));
  localStorage.setItem(
    "devfeed:extension-analytics-session",
    JSON.stringify({ id: 1, at: Date.now() - 1800001 }),
  );
  stop();
  stop = startExtensionAnalytics(network);
  await vi.waitFor(() => expect(network).toHaveBeenCalledTimes(4));
  const second = JSON.parse(String(network.mock.calls[3][1]?.body));
  expect(second.client_id).toBe(first.client_id);
  expect(second.session_id).not.toBe(1);
});
it("does not interfere with browsing when local storage is blocked", () => {
  const network = vi.fn();
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  expect(() => {
    stop = startExtensionAnalytics(network);
  }).not.toThrow();
  expect(network).not.toHaveBeenCalled();
});
