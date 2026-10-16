import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PrivateApiClient, PublicApiClient, UserApiError } from "@/lib/server/api-client";

vi.mock("@devfeed/telemetry/propagation", () => ({
  traceHeaders: () => ({ traceparent: "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01" }),
}));

beforeEach(() => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "https://public-api.internal");
  vi.stubEnv("DEVFEED_USER_API_URL", "https://user-api.internal");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("keeps public origin and caching policy when endpoint names resemble private routes", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Promise.resolve(Response.json({ name: "catalog" })));
  vi.stubGlobal("fetch", fetcher);
  const client = new PublicApiClient();
  await client.read("/v1/user/catalog?query=C%2B%2B");
  await client.read("/v2/catalog?query=C%2B%2B");
  for (const [url, options] of fetcher.mock.calls) {
    expect(url.origin).toBe("https://public-api.internal");
    expect(url.searchParams.get("query")).toBe("C++");
    expect(options).toMatchObject({
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        "Cache-Control": "max-age=600",
        traceparent: "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
      },
    });
    expect(options.headers.Cookie).toBeUndefined();
    expect(options.headers.Authorization).toBeUndefined();
  }
});

it("keeps private service and no-store policy across endpoint renames with and without cookies", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json({ items: [] })));
  vi.stubGlobal("fetch", fetcher);
  const client = new PrivateApiClient();
  await client.read("/v1/feed");
  await client.read("/v2/popular", {
    cookie:
      "devfeed_user_session=session; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor; devfeed_admin_session=secret; devfeed_user_x_click=click; _twclid=pixel; other=secret",
  });
  for (const [url, options] of fetcher.mock.calls) {
    expect(url.origin).toBe("https://user-api.internal");
    expect(options.cache).toBe("no-store");
    expect(options.headers["Cache-Control"]).toBe("no-store");
  }
  expect(fetcher.mock.calls[0][1].headers.Cookie).toBeUndefined();
  expect(fetcher.mock.calls[1][1].headers.Cookie).toBe(
    "devfeed_user_session=session; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor",
  );
});

it("binds a fresh public read and its longer deadline explicitly", async () => {
  const deadline = new AbortController();
  const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
  const fetcher = vi.fn().mockResolvedValue(Response.json({ entries: [] }));
  vi.stubGlobal("fetch", fetcher);
  await new PublicApiClient({ upstreamCache: "fresh", deadlineMs: 55_000 }).read("/inventory");
  expect(timeout).toHaveBeenCalledWith(55_000);
  const [, options] = fetcher.mock.calls[0];
  expect(options.headers["Cache-Control"]).toBe("no-store");
  expect(options.signal).toBe(deadline.signal);
  expect(options.cache).toBe("no-store");
});

it.each(["caller", "deadline"])("cancels a pending read on %s abort", async (reason) => {
  const caller = new AbortController();
  const deadline = new AbortController();
  vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
  const fetcher = vi.fn((_url: URL, options: RequestInit) => {
    const signal = options.signal!;
    return new Promise<Response>((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const read = new PublicApiClient().read("/feed", { signal: caller.signal });
  const rejection = expect(read).rejects.toEqual(new UserApiError(503));
  (reason === "caller" ? caller : deadline).abort();
  await rejection;
  expect(fetcher.mock.calls[0][1].signal?.aborted).toBe(true);
});

it.each([PublicApiClient, PrivateApiClient])(
  "preserves upstream status while hiding network errors and invalid JSON with %s",
  async (Client) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const client = new Client();
    fetcher.mockResolvedValueOnce(new Response(null, { status: 404 }));
    await expect(client.read("/article/missing")).rejects.toEqual(new UserApiError(404));
    fetcher.mockRejectedValueOnce(new Error("secret internal host"));
    await expect(client.read("/feed")).rejects.toEqual(new UserApiError(503));
    fetcher.mockResolvedValueOnce(new Response("invalid JSON"));
    await expect(client.read("/feed")).rejects.toEqual(new UserApiError(502));
  },
);

it.each([
  "https://other.example/v1/feed",
  "//other.example/v1/feed",
  "//public-api.internal/v1/feed",
  "/\\other.example/v1/feed",
  "relative/feed",
  "/v1/feed#fragment",
])("refuses the URL %s without making an upstream request", async (path) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(new PublicApiClient().read(path)).rejects.toEqual(new UserApiError(503));
  await expect(
    new PrivateApiClient().read(path, { cookie: "devfeed_user_session=secret" }),
  ).rejects.toEqual(new UserApiError(503));
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([
  "file:///internal/api",
  "https://user:secret@api.internal",
  "https://api.internal/v1",
  "https://api.internal?secret=value",
  "https://api.internal#fragment",
])("refuses malformed origin configuration %s without leaking credentials", async (origin) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", origin);
  vi.stubEnv("DEVFEED_USER_API_URL", origin);
  await expect(new PublicApiClient().read("/feed")).rejects.toEqual(new UserApiError(503));
  await expect(new PrivateApiClient().read("/feed")).rejects.toEqual(new UserApiError(503));
  expect(fetcher).not.toHaveBeenCalled();
});
