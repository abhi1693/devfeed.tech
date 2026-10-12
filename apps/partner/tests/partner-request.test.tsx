// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePartnerAccountRequest } from "../src/lib/partner-request";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const ACCOUNT = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const unavailable = vi.fn();
const options = {
  parse: (value: unknown) => value,
  errorMessage: "Could not load settings.",
  invalidMessage: "Could not confirm settings.",
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("scopes uncached reads to the account with browser credentials", async () => {
  const body = { product: null };
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(body));
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() =>
    usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable: unavailable }),
  );
  await expect(view.result.current("destination-preview", options)).resolves.toEqual(body);
  expect(fetcher).toHaveBeenCalledExactlyOnceWith(
    `/api/v1/partner/accounts/${ACCOUNT}/destination-preview`,
    expect.objectContaining({
      cache: "no-store",
      credentials: "same-origin",
      signal: expect.any(AbortSignal),
    }),
  );
  expect(unavailable).not.toHaveBeenCalled();
});

it.each([
  ["GET", 401, true, true],
  ["GET", 403, true, true],
  ["GET", 404, true, false],
  ["PUT", 403, false, false],
] as const)(
  "handles %s %s without confusing read access and write permission",
  async (method, status, clears, login) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status })));
    const view = renderHook(() =>
      usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable: unavailable }),
    );
    await expect(view.result.current("preferences", { ...options, method })).rejects.toThrow();
    expect(unavailable).toHaveBeenCalledTimes(clears ? 1 : 0);
    if (login) expect(router.replace).toHaveBeenCalledExactlyOnceWith("/login");
    else expect(router.replace).not.toHaveBeenCalled();
  },
);

it.each([
  ["account", "fetch"],
  ["account", "json"],
  ["unmount", "fetch"],
  ["unmount", "json"],
] as const)("cancels delayed %s-scoped %s results", async (change, phase) => {
  const responseReady = deferred<Response>();
  const jsonReady = deferred<unknown>();
  const json = vi.fn(() => jsonReady.promise);
  const response = { status: 200, ok: true, json } as unknown as Response;
  const fetcher = vi
    .fn<typeof fetch>()
    .mockReturnValue(phase === "fetch" ? responseReady.promise : Promise.resolve(response));
  vi.stubGlobal("fetch", fetcher);
  const parse = vi.fn((value: unknown) => value);
  const view = renderHook(
    ({ accountId }) => usePartnerAccountRequest({ accountId, onUnavailable: unavailable }),
    { initialProps: { accountId: ACCOUNT } },
  );
  const pending = view.result.current("destination-preview", { ...options, parse });
  const settled = pending.then(
    () => "fulfilled",
    () => "rejected",
  );
  if (phase === "json") await waitFor(() => expect(json).toHaveBeenCalledOnce());
  const signal = fetcher.mock.calls[0][1]!.signal!;
  if (change === "account") view.rerender({ accountId: OTHER });
  else view.unmount();
  expect(signal.aborted).toBe(true);
  responseReady.resolve(new Response(null, { status: 401 }));
  jsonReady.resolve({ product: null });
  expect(await settled).toBe("rejected");
  expect(parse).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

it("uses the latest access callback without restarting a read", async () => {
  const response = deferred<Response>();
  const fetcher = vi.fn<typeof fetch>().mockReturnValue(response.promise);
  const currentUnavailable = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(
    ({ onUnavailable }) => usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable }),
    { initialProps: { onUnavailable: unavailable } },
  );
  const pending = view.result.current("destination-preview", options);
  const settled = pending.catch(() => undefined);
  view.rerender({ onUnavailable: currentUnavailable });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(false);
  response.resolve(new Response(null, { status: 401 }));
  await settled;
  expect(currentUnavailable).toHaveBeenCalledOnce();
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).toHaveBeenCalledExactlyOnceWith("/login");
});

it("invalidates other in-flight account reads before publishing an access loss", async () => {
  const body = deferred<unknown>();
  const json = vi.fn(() => body.promise);
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce({ status: 200, ok: true, json } as unknown as Response)
    .mockResolvedValueOnce(new Response(null, { status: 403 }));
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() =>
    usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable: unavailable }),
  );
  const old = view.result.current("destination-preview", options);
  const settled = old.then(
    () => "fulfilled",
    () => "rejected",
  );
  await waitFor(() => expect(json).toHaveBeenCalledOnce());
  await expect(view.result.current("audience-topics?dimension=tools", options)).rejects.toThrow();
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(true);
  body.resolve({ product: null });
  expect(await settled).toBe("rejected");
  expect(unavailable).toHaveBeenCalledOnce();
});

it.each(["json", "schema"] as const)(
  "rejects invalid %s without clearing account access",
  async (failure) => {
    const response = failure === "json" ? new Response("{") : Response.json({ product: "invalid" });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    const view = renderHook(() =>
      usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable: unavailable }),
    );
    await expect(
      view.result.current("destination-preview", {
        ...options,
        parse: () => {
          throw new Error("private decoder details");
        },
      }),
    ).rejects.toThrow(options.invalidMessage);
    expect(unavailable).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  },
);

it("honors caller cancellation while a response body is still decoding", async () => {
  const body = deferred<unknown>();
  const json = vi.fn(() => body.promise);
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue({ status: 200, ok: true, json } as unknown as Response),
  );
  const view = renderHook(() =>
    usePartnerAccountRequest({ accountId: ACCOUNT, onUnavailable: unavailable }),
  );
  const controller = new AbortController();
  const pending = view.result.current("destination-preview", {
    ...options,
    signal: controller.signal,
  });
  const settled = pending.then(
    () => "fulfilled",
    () => "rejected",
  );
  await waitFor(() => expect(json).toHaveBeenCalledOnce());
  act(() => controller.abort());
  body.resolve({ product: null });
  expect(await settled).toBe("rejected");
  expect(unavailable).not.toHaveBeenCalled();
});
