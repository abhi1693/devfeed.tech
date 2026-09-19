// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeferredClarity } from "@/components/deferred-clarity";

const { init } = vi.hoisted(() => ({ init: vi.fn() }));
vi.mock("@microsoft/clarity", () => ({ default: { init } }));

afterEach(() => {
  cleanup();
  init.mockClear();
});

it("defers Clarity until reader interaction and initializes it once", () => {
  render(<DeferredClarity projectId="clarity-test" />);
  expect(init).not.toHaveBeenCalled();
  fireEvent.pointerDown(window);
  fireEvent.scroll(window);
  fireEvent.keyDown(window);
  expect(init).toHaveBeenCalledTimes(1);
  expect(init).toHaveBeenCalledWith("clarity-test");
});

it("loads Clarity on page exit without interaction", () => {
  render(<DeferredClarity projectId="clarity-test" />);
  fireEvent(window, new Event("pagehide"));
  expect(init).toHaveBeenCalledWith("clarity-test");
});

it("removes deferred listeners when unmounted", () => {
  const view = render(<DeferredClarity projectId="clarity-test" />);
  view.unmount();
  fireEvent.pointerDown(window);
  fireEvent(window, new Event("pagehide"));
  expect(init).not.toHaveBeenCalled();
});

it("does not fail the reader when Clarity initialization throws", () => {
  init.mockImplementationOnce(() => {
    throw new Error("blocked by browser");
  });
  render(<DeferredClarity projectId="clarity-test" />);
  expect(() => fireEvent.pointerDown(window)).not.toThrow();
});
