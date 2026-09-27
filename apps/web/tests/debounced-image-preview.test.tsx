// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useDebouncedImagePreview } from "@devfeed/ui/use-debounced-image-preview";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("shows an initial URL immediately, hides stale previews, and cancels superseded timers", () => {
  vi.useFakeTimers();
  const { result, rerender, unmount } = renderHook(
    ({ url }: { url: string | null }) => useDebouncedImagePreview(url),
    { initialProps: { url: "https://example.test/a.png" as string | null } },
  );
  expect(result.current).toBe("https://example.test/a.png");
  rerender({ url: "https://example.test/b.png" });
  expect(result.current).toBeNull();
  act(() => vi.advanceTimersByTime(200));
  rerender({ url: "https://example.test/c.png" });
  act(() => vi.advanceTimersByTime(299));
  expect(result.current).toBeNull();
  act(() => vi.advanceTimersByTime(1));
  expect(result.current).toBe("https://example.test/c.png");
  rerender({ url: null });
  expect(result.current).toBeNull();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
