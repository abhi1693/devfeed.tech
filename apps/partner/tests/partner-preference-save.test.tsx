// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePartnerPreferenceSave } from "../src/lib/use-partner-preference-save";
import { partnerAccount } from "./partner-fixtures";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account = partnerAccount();
const OTHER = "22222222-2222-4222-8222-222222222222";
const saved = vi.fn();
const unavailable = vi.fn();
function options(changes: Partial<Parameters<typeof usePartnerPreferenceSave>[0]> = {}) {
  return {
    accountId: account.id,
    csrfToken: "first-csrf",
    onSaved: saved,
    onUnavailable: unavailable,
    successMessage: "Preferences saved.",
    errorMessage: "Could not save preferences.",
    confirmationMessage: "Could not confirm saved preferences.",
    ...changes,
  };
}
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

it("sends a single partial PUT for same-tick submissions and publishes the full account", async () => {
  const response = deferred<Response>();
  const fetcher = vi.fn<typeof fetch>().mockReturnValue(response.promise);
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() => usePartnerPreferenceSave(options()));
  const writes: Promise<void>[] = [];
  act(() => {
    writes.push(view.result.current.save({ ad_formats: [] }));
    writes.push(view.result.current.save({ ad_formats: [] }));
  });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(view.result.current.saving).toBe(true);
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(`/api/v1/partner/accounts/${account.id}/preferences`);
  expect(init!.method).toBe("PUT");
  expect(JSON.parse(init!.body as string)).toEqual({ ad_formats: [] });
  expect(new Headers(init!.headers).get("x-csrf-token")).toBe("first-csrf");
  await act(async () => {
    response.resolve(Response.json(account));
    await Promise.all(writes);
  });
  expect(saved).toHaveBeenCalledExactlyOnceWith(account);
  expect(view.result.current).toMatchObject({
    saving: false,
    error: "",
    message: "Preferences saved.",
  });
  act(() => view.result.current.clearMessage());
  expect(view.result.current.message).toBe("");
});

it.each(["account", "csrf"] as const)(
  "ignores old JSON and old completion while a new %s save is pending",
  async (change) => {
    const oldBody = deferred<unknown>();
    const json = vi.fn(() => oldBody.promise);
    const newResponse = deferred<Response>();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({ status: 200, ok: true, json } as unknown as Response)
      .mockReturnValueOnce(newResponse.promise);
    vi.stubGlobal("fetch", fetcher);
    const view = renderHook((props) => usePartnerPreferenceSave(props), {
      initialProps: options(),
    });
    let oldWrite!: Promise<void>;
    act(() => {
      oldWrite = view.result.current.save({ ad_formats: [] });
    });
    await waitFor(() => expect(json).toHaveBeenCalledOnce());
    const next = { ...account, id: change === "account" ? OTHER : account.id };
    view.rerender(options({ accountId: next.id, csrfToken: "next-csrf" }));
    expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(true);
    expect(view.result.current.saving).toBe(false);
    let newWrite!: Promise<void>;
    act(() => {
      newWrite = view.result.current.save({ ad_formats: ["feed"] });
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(new Headers(fetcher.mock.calls[1][1]!.headers).get("x-csrf-token")).toBe("next-csrf");
    await act(async () => {
      oldBody.resolve(account);
      await oldWrite;
    });
    expect(saved).not.toHaveBeenCalled();
    expect(view.result.current).toMatchObject({ saving: true, error: "", message: "" });
    await act(async () => {
      newResponse.resolve(Response.json(next));
      await newWrite;
    });
    expect(saved).toHaveBeenCalledExactlyOnceWith(next);
    expect(view.result.current.saving).toBe(false);
    expect(unavailable).not.toHaveBeenCalled();
  },
);

it("does not publish JSON that finishes after the form unmounts", async () => {
  const body = deferred<unknown>();
  const json = vi.fn(() => body.promise);
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue({ status: 200, ok: true, json } as unknown as Response);
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() => usePartnerPreferenceSave(options()));
  let write!: Promise<void>;
  act(() => {
    write = view.result.current.save({ ad_formats: [] });
  });
  await waitFor(() => expect(json).toHaveBeenCalledOnce());
  view.unmount();
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(true);
  body.resolve(account);
  await write;
  expect(saved).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
});

it("publishes to the latest saved callback without restarting an unchanged account save", async () => {
  const body = deferred<unknown>();
  const json = vi.fn(() => body.promise);
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue({ status: 200, ok: true, json } as unknown as Response);
  const currentSaved = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook((props) => usePartnerPreferenceSave(props), { initialProps: options() });
  let write!: Promise<void>;
  act(() => {
    write = view.result.current.save({ ad_formats: [] });
  });
  await waitFor(() => expect(json).toHaveBeenCalledOnce());
  view.rerender(options({ onSaved: currentSaved }));
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(false);
  await act(async () => {
    body.resolve(account);
    await write;
  });
  expect(currentSaved).toHaveBeenCalledExactlyOnceWith(account);
  expect(saved).not.toHaveBeenCalled();
});

it("rotates write CSRF without cancelling same-account preview reads or applying stale access errors", async () => {
  const readResponse = deferred<Response>();
  const writeResponse = deferred<Response>();
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation((url) =>
      String(url).endsWith("destination-preview") ? readResponse.promise : writeResponse.promise,
    );
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook((props) => usePartnerPreferenceSave(props), { initialProps: options() });
  const read = view.result.current.request("destination-preview", {
    parse: (value: unknown) => value,
    errorMessage: "Preview unavailable.",
  });
  let write!: Promise<void>;
  act(() => {
    write = view.result.current.save({ outbound_destination: "listing_url" });
  });
  view.rerender(options({ csrfToken: "next-csrf" }));
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(false);
  expect(fetcher.mock.calls[1][1]!.signal!.aborted).toBe(true);
  await act(async () => {
    writeResponse.resolve(new Response(null, { status: 401 }));
    await write;
  });
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  expect(fetcher.mock.calls[0][1]!.signal!.aborted).toBe(false);
  readResponse.resolve(Response.json({ product: null }));
  await expect(read).resolves.toEqual({ product: null });
});

it.each([
  { id: account.id, ad_formats: [] },
  { ...account, id: OTHER },
  { ...account, outbound_destination: "javascript:invalid" },
  { ...account, audience: { ...account.audience, experience_levels: ["unknown"] } },
])("rejects incomplete, foreign or malformed account summaries", async (value) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(Response.json(value)));
  const view = renderHook(() => usePartnerPreferenceSave(options()));
  await act(async () => {
    await view.result.current.save({ ad_formats: [] });
  });
  expect(view.result.current).toMatchObject({
    saving: false,
    error: "Could not confirm saved preferences.",
    message: "",
  });
  expect(saved).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
});

it("retains a retryable 403 error and can save successfully afterward", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(new Response(null, { status: 403 }))
    .mockResolvedValueOnce(Response.json(account));
  vi.stubGlobal("fetch", fetcher);
  const view = renderHook(() => usePartnerPreferenceSave(options()));
  await act(async () => {
    await view.result.current.save({ ad_formats: [] });
  });
  expect(view.result.current.error).toContain("Refresh the page and try again");
  expect(view.result.current.saving).toBe(false);
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  await act(async () => {
    await view.result.current.save({ ad_formats: [] });
  });
  expect(saved).toHaveBeenCalledExactlyOnceWith(account);
  expect(view.result.current).toMatchObject({
    saving: false,
    error: "",
    message: "Preferences saved.",
  });
});
