import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET, POST as legacyPOST } from "@/app/api/v1/search/route";
import { POST } from "@/app/api/v1/search/analytics/click/route";
import { searchClick } from "@/lib/server/search-click";
import { getSearch } from "@/lib/api";

vi.mock("@/lib/api", () => ({ getSearch: vi.fn(), UserApiError: class extends Error {} }));
vi.mock("@devfeed/telemetry/propagation", () => ({
  traceHeaders: () => ({ traceparent: "trace" }),
}));

const event = JSON.stringify({
  query: "Python",
  result_kind: "articles",
  result_id: "00000000-0000-0000-0000-000000000001",
  click_token: "server-receipt",
});
const request = (body: BodyInit = event, headers?: HeadersInit, signal?: AbortSignal) =>
  new Request("https://devfeed.tech/api/v1/search/analytics/click?upstream=evil", {
    method: "POST",
    body,
    headers,
    signal,
    duplex: "half",
  } as RequestInit);

beforeEach(() => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("routes the actual reader click path and legacy POST through the same handler", async () => {
  expect(POST).toBe(searchClick);
  expect(legacyPOST).toBe(searchClick);
  for (const route of [POST, legacyPOST]) {
    const response = await route(
      request(event, {
        Authorization: "private",
        Cookie: "private",
        "X-Forwarded-Host": "evil.example",
        "Content-Type": "text/html",
      }),
    );
    expect(response.status).toBe(204);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [url, options] = vi.mocked(fetch).mock.calls.at(-1)!;
    expect(String(url)).toBe("http://public-api:8000/v1/search/analytics/click");
    expect(options).toMatchObject({ method: "POST", redirect: "manual", cache: "no-store" });
    expect(options?.headers).toEqual({ "Content-Type": "application/json", traceparent: "trace" });
    expect(new TextDecoder().decode(options?.body as ArrayBuffer)).toBe(event);
  }
});

it.each(["x".repeat(4096), "é".repeat(2048)])("accepts exactly 4096 UTF-8 bytes", async (body) => {
  expect((await POST(request(body))).status).toBe(204);
  expect((vi.mocked(fetch).mock.calls[0][1]?.body as ArrayBuffer).byteLength).toBe(4096);
});

it("accepts a full-length non-BMP query when its JSON surrogate pairs are escaped", async () => {
  const query = "𐐀".repeat(200);
  const body = JSON.stringify({ ...JSON.parse(event), query }).replaceAll("𐐀", "\\ud801\\udc00");
  expect(new TextEncoder().encode(body).byteLength).toBeGreaterThan(2048);
  expect((await POST(request(body))).status).toBe(204);
  const sent = new TextDecoder().decode(vi.mocked(fetch).mock.calls[0][1]?.body as ArrayBuffer);
  expect(sent).toBe(body);
  expect(JSON.parse(sent)).toEqual({ ...JSON.parse(event), query });
});

it("rejects declared oversized bodies without calling upstream", async () => {
  const response = await POST(request(event, { "Content-Length": "4097" }));
  expect(response.status).toBe(413);
  expect(await response.json()).toEqual({ detail: "Request too large" });
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(fetch).not.toHaveBeenCalled();
});

it("cancels oversized chunked multibyte bodies without calling upstream", async () => {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("é".repeat(1024)));
      controller.enqueue(new TextEncoder().encode("é".repeat(1025)));
    },
    cancel,
  });
  const response = await POST(request(stream));
  expect(response.status).toBe(413);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(cancel).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
});

it("returns a private 408 for a stalled body without calling upstream", async () => {
  vi.useFakeTimers();
  const cancel = vi.fn(() => new Promise<void>(() => {}));
  const incoming = request(new ReadableStream<Uint8Array>({ cancel }));
  const pending = POST(incoming);
  await vi.advanceTimersByTimeAsync(1999);
  expect(cancel).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  const response = await pending;
  expect(response.status).toBe(408);
  expect(await response.json()).toEqual({ detail: "Incomplete request" });
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(cancel).toHaveBeenCalledOnce();
  expect(incoming.body!.locked).toBe(false);
  expect(fetch).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it.each([400, 422, 429])(
  "preserves upstream status %s and its retry delay without private content",
  async (status) => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("private upstream details", {
        status,
        headers: { "Retry-After": "42", "X-Private": "secret" },
      }),
    );
    const response = await POST(request());
    expect(response.status).toBe(status);
    expect(response.headers.get("retry-after")).toBe("42");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("x-private")).toBeNull();
    expect(await response.text()).toBe("");
  },
);

it.each([302, 500, 503])("sanitizes upstream failures and redirects (%s)", async (status) => {
  vi.mocked(fetch).mockResolvedValue(
    new Response("private upstream details", {
      status,
      headers: { Location: "https://evil.example", "Retry-After": "1" },
    }),
  );
  const response = await POST(request());
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ detail: "Search analytics unavailable" });
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(response.headers.get("location")).toBeNull();
  expect(response.headers.get("retry-after")).toBe("1");
});

it("returns a sanitized failure for network or body-read errors", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("Private URL and credentials"));
  const networkFailure = await POST(request());
  expect(networkFailure.status).toBe(503);
  expect(await networkFailure.json()).toEqual({ detail: "Search analytics unavailable" });
  vi.mocked(fetch).mockClear();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("Private client body"));
    },
  });
  const bodyFailure = await POST(request(stream));
  expect(bodyFailure.status).toBe(503);
  expect(await bodyFailure.json()).toEqual({ detail: "Search analytics unavailable" });
  expect(bodyFailure.headers.get("cache-control")).toBe("no-store");
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["timeout", "client"])(
  "bounds upstream work and propagates %s cancellation",
  async (source) => {
    const timeout = new AbortController();
    const client = new AbortController();
    const timeoutFactory = vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeout.signal);
    let upstreamSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((_url, init) => {
      upstreamSignal = init?.signal ?? undefined;
      return new Promise((_resolve, reject) => {
        upstreamSignal!.addEventListener("abort", () => reject(upstreamSignal!.reason), {
          once: true,
        });
      });
    });
    const pending = POST(request(event, undefined, client.signal));
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(timeoutFactory).toHaveBeenCalledWith(1500);
    (source === "timeout" ? timeout : client).abort(new Error("Private cancellation reason"));
    const response = await pending;
    expect(upstreamSignal?.aborted).toBe(true);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "Search analytics unavailable" });
  },
);

it("rejects NUL search requests before upstream work and keeps punctuation literal", async () => {
  const invalid = await GET(
    new Request("https://devfeed.tech/api/v1/search?" + new URLSearchParams({ q: "python\0" })),
  );
  expect(invalid.status).toBe(422);
  expect(invalid.headers.get("cache-control")).toBe("no-store");
  expect(getSearch).not.toHaveBeenCalled();
  const query = 'C++ <img alt="example"> SELECT';
  vi.mocked(getSearch).mockResolvedValue({ query, sections: {} });
  expect(
    (
      await GET(
        new Request("https://devfeed.tech/api/v1/search?" + new URLSearchParams({ q: query })),
      )
    ).status,
  ).toBe(200);
  expect(getSearch).toHaveBeenCalledWith(
    query,
    undefined,
    "1",
    expect.any(AbortSignal),
    expect.any(Object),
  );
});
