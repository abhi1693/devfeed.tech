// @vitest-environment jsdom
import { lazy, useEffect } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LoadingReveal } from "@/components/loading-reveal";
import { SuspenseReveal } from "@/components/suspense-reveal";

const fallback = <div role="status">Loading</div>;
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("keeps loading content unmounted until ready and unmounts it when loading resumes", () => {
  const mounted = vi.fn();
  const unmounted = vi.fn();
  function Content() {
    useEffect(() => {
      mounted();
      return unmounted;
    }, []);
    return <button>Result</button>;
  }
  const tree = (loading: boolean) => (
    <LoadingReveal loading={loading} fallback={fallback}>
      <Content />
    </LoadingReveal>
  );
  const view = render(tree(true));
  expect(mounted).not.toHaveBeenCalled();
  expect(view.container.querySelector(".reader-loading-content")).toBeNull();
  view.rerender(tree(false));
  expect(mounted).toHaveBeenCalledOnce();
  expect(screen.queryByRole("status")).toBeNull();
  view.rerender(tree(true));
  expect(unmounted).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByRole("status")).toBeTruthy();
});

it("renders initially ready content without introducing a placeholder", () => {
  render(
    <LoadingReveal loading={false} fallback={fallback}>
      Ready
    </LoadingReveal>,
  );
  expect(screen.queryByText("Loading")).toBeNull();
  expect(screen.getByText("Ready")).toBeTruthy();
});

it("cancels placeholder removal when loading resumes and on unmount", () => {
  vi.useFakeTimers();
  const tree = (loading: boolean) => (
    <LoadingReveal loading={loading} fallback={fallback}>
      Ready
    </LoadingReveal>
  );
  const view = render(tree(true));
  view.rerender(tree(false));
  act(() => vi.advanceTimersByTime(100));
  view.rerender(tree(true));
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(180));
  expect(screen.getByRole("status")).toBeTruthy();
  view.rerender(tree(false));
  act(() => vi.advanceTimersByTime(179));
  expect(screen.getByText("Loading").parentElement?.hasAttribute("inert")).toBe(true);
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByText("Loading")).toBeNull();
  view.rerender(tree(true));
  view.rerender(tree(false));
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it("starts suspended content inside a hidden wrapper and reveals it without remounting", async () => {
  vi.useFakeTimers();
  const mounted = vi.fn();
  const unmounted = vi.fn();
  function Content() {
    useEffect(() => {
      mounted();
      return unmounted;
    }, []);
    return <button>Result</button>;
  }
  let resolve!: (value: { default: typeof Content }) => void;
  const promise = new Promise<{ default: typeof Content }>((done) => {
    resolve = done;
  });
  const loader = vi.fn(() => promise);
  const Deferred = lazy(loader);
  const view = render(
    <SuspenseReveal fallback={fallback}>
      <Deferred />
    </SuspenseReveal>,
  );
  const wrapper = view.container.querySelector<HTMLElement>(".reader-loading-content")!;
  expect(loader).toHaveBeenCalledOnce();
  expect(mounted).not.toHaveBeenCalled();
  expect(wrapper.style.visibility).toBe("hidden");
  expect(wrapper.style.height).toBe("0px");
  expect(screen.getByRole("status")).toBeTruthy();
  await act(async () => {
    resolve({ default: Content });
    await promise;
  });
  expect(mounted).toHaveBeenCalledOnce();
  expect(wrapper.style.visibility).toBe("");
  expect(screen.getByRole("button", { name: "Result" })).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByText("Loading").parentElement?.hasAttribute("inert")).toBe(true);
  act(() => vi.advanceTimersByTime(179));
  expect(screen.getByText("Loading")).toBeTruthy();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByText("Loading")).toBeNull();
  expect(unmounted).not.toHaveBeenCalled();
  view.unmount();
  expect(unmounted).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});
