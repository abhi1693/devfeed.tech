// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerPlacements } from "@/components/partner-placements";
import { createColumnRequestQueue } from "@/lib/column-feed";
import { userRequest } from "@/lib/user";
import { partnerReceipt, sponsoredPlacement } from "./partner-placement-fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: { user_id: "reader", csrf_token: "csrf" }, loading: false }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));

function busyQueue() {
  const queue = createColumnRequestQueue();
  const complete: (() => void)[] = [];
  const requests = Array.from({ length: 3 }, () =>
    queue.run(
      () => new Promise<void>((resolve) => complete.push(resolve)),
      new AbortController().signal,
    ),
  );
  return {
    queue,
    complete: (index: number) => complete[index](),
    finish: async () => {
      complete.forEach((resolve) => resolve());
      await Promise.all(requests);
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((duration) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), duration);
    return controller.signal;
  });
  vi.mocked(userRequest)
    .mockReset()
    .mockResolvedValue({
      items: [
        {
          ...sponsoredPlacement,
          receipt: partnerReceipt({ assetId: sponsoredPlacement.asset_id }),
        },
      ],
    });
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("shares the three-request column queue and starts its placement deadline after admission", async () => {
  const busy = busyQueue();
  await act(async () => {});
  render(
    <PartnerPlacements
      surface="feed"
      ad_formats={["feed"]}
      layout="columns"
      maxItems={1}
      requestQueue={busy.queue}
    />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(userRequest).not.toHaveBeenCalled();
  await act(async () => busy.complete(0));
  expect(userRequest).toHaveBeenCalledOnce();
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeTruthy();
  const signal = vi.mocked(userRequest).mock.calls[0][1]!.signal!;
  expect(signal.aborted).toBe(false);
  await busy.finish();
});

it("cancels a queued placement when its owning column unmounts", async () => {
  const busy = busyQueue();
  await act(async () => {});
  const mounted = render(
    <PartnerPlacements
      surface="feed"
      ad_formats={["feed"]}
      layout="columns"
      maxItems={1}
      requestQueue={busy.queue}
    />,
  );
  await act(async () => {});
  mounted.unmount();
  await act(async () => busy.finish());
  expect(userRequest).not.toHaveBeenCalled();
});
