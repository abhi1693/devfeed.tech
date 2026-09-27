// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { ChimelyClient } from "@chimely/client";
import { createInboxClient, inboxPath } from "@/lib/inbox";
import { returnToLogin } from "@/lib/api/client";
vi.mock("@chimely/client", () => ({ ChimelyClient: vi.fn(function () {}) }));
vi.mock("@/lib/api/client", () => ({ returnToLogin: vi.fn() }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
it.each([401, 403, 200])(
  "uses native fetch and returns to login on 401 or 403 (%s)",
  async (status) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const response = Response.json({}, { status });
    const fetcher = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetcher);
    createInboxClient({ enabled: true, environment: "test", subscriber_id: "admin" }, "csrf");
    const config = vi.mocked(ChimelyClient).mock.lastCall![0];
    expect(config.serverUrl).toBe(inboxPath);
    expect(config.createEventSource).toBeUndefined();
    expect(await config.fetchFn!(inboxPath + "/v1/inbox/messages")).toBe(response);
    expect(fetcher).toHaveBeenCalledWith(
      window.location.origin + inboxPath + "/v1/inbox/messages",
      expect.any(Object),
    );
    expect(returnToLogin).toHaveBeenCalledTimes(status === 401 || status === 403 ? 1 : 0);
  },
);
