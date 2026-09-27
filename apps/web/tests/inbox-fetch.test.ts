// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createInboxFetch } from "@devfeed/ui/inbox-fetch";

const origin = "https://reader.example";
const path = "/api/v1/user/notifications/chimely";
const endpoint = `${path}/v1/inbox/messages`;
const request = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
  Response.json({}),
);
const fetchInbox = createInboxFetch({ origin: () => origin, path, csrfToken: "csrf", request });
beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});
afterEach(() => {
  vi.restoreAllMocks();
  request.mockClear();
});

it.each([
  "https://evil.example" + endpoint,
  "/api/v1/admin/notifications/chimely/v1/inbox/messages",
  path + "/v1/inbox-other/messages",
  path + "/v1/inbox/../../other",
])("rejects endpoints outside the configured inbox: %s", async (url) => {
  await expect(fetchInbox(url)).rejects.toThrow("Invalid inbox endpoint");
  expect(request).not.toHaveBeenCalled();
});

it("preserves request data while enforcing credential, cache, redirect and CSRF rules", async () => {
  const headers = new Headers({ "Content-Type": "application/json", "X-CSRF-Token": "old" });
  await fetchInbox(endpoint, {
    method: "POST",
    headers,
    body: "{}",
    credentials: "omit",
    cache: "force-cache",
    redirect: "follow",
  });
  const [url, init] = request.mock.calls[0];
  expect(url).toBe(origin + endpoint);
  expect(init).toMatchObject({
    method: "POST",
    body: "{}",
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
  });
  expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf");
  expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  expect(headers.get("X-CSRF-Token")).toBe("old");
});

it.each([undefined, "GET", "HEAD"])(
  "gates inactive reads (%s) but permits mutations",
  async (method) => {
    vi.mocked(document.hasFocus).mockReturnValue(false);
    await expect(fetchInbox(endpoint, { method })).rejects.toMatchObject({ name: "AbortError" });
    expect(request).not.toHaveBeenCalled();
    await fetchInbox(endpoint, { method: "POST" });
    expect(request).toHaveBeenCalledTimes(1);
  },
);

it("blocks reads on hidden pages and does not add CSRF to active reads", async () => {
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  await expect(fetchInbox(endpoint)).rejects.toMatchObject({ name: "AbortError" });
  visibility.mockReturnValue("visible");
  await fetchInbox(endpoint);
  expect(new Headers(request.mock.calls[0][1].headers).has("X-CSRF-Token")).toBe(false);
});

it.each([false, true])(
  "adds a 15-second timeout with caller cancellation: %s",
  async (callerSignal) => {
    const timeout = new AbortController();
    const caller = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeout.signal);
    await fetchInbox(endpoint, callerSignal ? { signal: caller.signal } : undefined);
    const signal = request.mock.calls[0][1].signal!;
    expect(timeoutSpy).toHaveBeenCalledWith(15_000);
    expect(signal.aborted).toBe(false);
    if (callerSignal) caller.abort();
    else timeout.abort();
    expect(signal.aborted).toBe(true);
  },
);

it("retains the timeout when combining a caller signal", async () => {
  const timeout = new AbortController();
  vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeout.signal);
  await fetchInbox(endpoint, { signal: new AbortController().signal });
  timeout.abort();
  expect(request.mock.calls[0][1].signal!.aborted).toBe(true);
});
