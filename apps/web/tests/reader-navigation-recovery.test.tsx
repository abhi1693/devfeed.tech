// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ReaderNavigationRecovery } from "@/components/reader-navigation-recovery";
import { beginReaderNavigation, useReaderRouter } from "@/lib/reader-navigation";
import { configureReaderRuntime } from "@/lib/reader-runtime";

const route = vi.hoisted(() => ({
  pathname: "/latest",
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => route.pathname,
  useSearchParams: () => new URLSearchParams(route.search),
  useRouter: () => ({ push: route.push, replace: route.replace }),
}));
const reload = vi.fn();
let location: URL;
beforeEach(() => {
  vi.useFakeTimers();
  route.pathname = "/latest";
  route.search = "";
  location = new URL("https://reader.test/latest");
  configureReaderRuntime({
    request: fetch,
    publicOrigin: location.origin,
    location: () => location,
    reload,
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("shows progress and recovers a transition whose URL changed without a render", () => {
  render(<ReaderNavigationRecovery />);
  act(() => beginReaderNavigation("/sources?offset=60#catalog"));
  expect(screen.getByRole("status").textContent).toBe("Loading page…");
  location = new URL("https://reader.test/sources?offset=60#catalog");
  act(() => vi.advanceTimersByTime(9999));
  expect(reload).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1));
  expect(reload).toHaveBeenCalledExactlyOnceWith("/sources?offset=60#catalog");
  expect(screen.queryByRole("status")).toBeNull();
});

it("cancels recovery when the route or query commits", () => {
  const view = render(<ReaderNavigationRecovery />);
  act(() => beginReaderNavigation("/sources"));
  route.pathname = "/sources";
  view.rerender(<ReaderNavigationRecovery />);
  expect(screen.queryByRole("status")).toBeNull();
  location = new URL("https://reader.test/sources");
  act(() => beginReaderNavigation("/sources?offset=60"));
  route.search = "offset=60";
  view.rerender(<ReaderNavigationRecovery />);
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).not.toHaveBeenCalled();
});

it("keeps only the latest destination and ignores external URLs and same-page links", () => {
  render(<ReaderNavigationRecovery />);
  act(() => {
    beginReaderNavigation("/sources");
    vi.advanceTimersByTime(5000);
    beginReaderNavigation("/topics");
    beginReaderNavigation("https://other.test/topics");
  });
  act(() => vi.advanceTimersByTime(5000));
  expect(reload).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(5000));
  expect(reload).toHaveBeenCalledExactlyOnceWith("/topics");
  reload.mockClear();
  act(() => beginReaderNavigation("#main"));
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).not.toHaveBeenCalled();
});

it("cancels timers when the document leaves or the component unmounts", () => {
  const view = render(<ReaderNavigationRecovery />);
  act(() => beginReaderNavigation("/sources"));
  fireEvent(window, new Event("pagehide"));
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).not.toHaveBeenCalled();
  act(() => beginReaderNavigation("/topics"));
  view.unmount();
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).not.toHaveBeenCalled();
});

it("keeps recovering the latest intent if a superseded page commits, but respects browser Back", () => {
  const view = render(<ReaderNavigationRecovery />);
  act(() => {
    beginReaderNavigation("/sources");
    beginReaderNavigation("/topics");
  });
  route.pathname = "/sources";
  view.rerender(<ReaderNavigationRecovery />);
  expect(screen.getByRole("status")).toBeTruthy();
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).toHaveBeenCalledExactlyOnceWith("/topics");
  reload.mockClear();
  act(() => beginReaderNavigation("/topics"));
  fireEvent(window, new PopStateEvent("popstate"));
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).not.toHaveBeenCalled();
});

it("also recovers programmatic navigation from search and notifications", () => {
  function Navigation() {
    const router = useReaderRouter();
    return <button onClick={() => router.push("/sources", { scroll: false })}>Open sources</button>;
  }
  render(
    <>
      <ReaderNavigationRecovery />
      <Navigation />
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open sources" }));
  expect(route.push).toHaveBeenCalledWith("/sources", { scroll: false });
  act(() => vi.advanceTimersByTime(10_000));
  expect(reload).toHaveBeenCalledExactlyOnceWith("/sources");
});
