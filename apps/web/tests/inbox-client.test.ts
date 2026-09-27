// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { ChimelyClient } from "@chimely/client";
import { createInboxClient, inboxPath } from "@/lib/inbox";
import { readerRequest } from "@/lib/reader-runtime";
vi.mock("@chimely/client", () => ({ ChimelyClient: vi.fn(function () {}) }));
vi.mock("@/lib/reader-runtime", () => ({
  readerPublicOrigin: () => "https://website.example",
  readerRequest: vi.fn(),
}));
afterEach(() => vi.restoreAllMocks());
it.each([401, 403, 200])(
  "uses reader transport and expires the session only on 401 (%s)",
  async (status) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const dispatch = vi.spyOn(window, "dispatchEvent");
    const response = Response.json({}, { status });
    vi.mocked(readerRequest).mockResolvedValue(response);
    createInboxClient({ enabled: true, environment: "test", subscriber_id: "user" }, "csrf");
    const config = vi.mocked(ChimelyClient).mock.lastCall![0];
    expect(config.serverUrl).toBe("https://website.example" + inboxPath);
    expect(config.createEventSource).toBeTypeOf("function");
    expect(await config.fetchFn!(config.serverUrl + "/v1/inbox/messages")).toBe(response);
    expect(readerRequest).toHaveBeenCalledWith(
      config.serverUrl + "/v1/inbox/messages",
      expect.any(Object),
    );
    expect(dispatch).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
    if (status === 401) expect(dispatch.mock.calls[0][0].type).toBe("devfeed:user-session-expired");
  },
);
