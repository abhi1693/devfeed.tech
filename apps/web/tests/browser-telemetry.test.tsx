// @vitest-environment jsdom
import { cleanup, render, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({
  initialize: vi.fn(),
  setView: vi.fn(),
  pushEvent: vi.fn(),
  pathname: "/latest",
}));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname }));
vi.mock("../../../packages/telemetry/src/browser-initialize", () => ({
  initializeBrowserTelemetry: mocks.initialize,
}));
import { BrowserTelemetry } from "@devfeed/telemetry/browser";
it("honors disabled/DNT, initializes once, then tracks client navigation", async () => {
  const settings = { enabled: false, app: "web" as const, version: "test", environment: "test" };
  vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
  Object.defineProperty(navigator, "doNotTrack", { configurable: true, get: () => "1" });
  const dnt = vi.spyOn(navigator, "doNotTrack", "get").mockReturnValue("1");
  const first = render(<BrowserTelemetry {...settings} />);
  first.rerender(<BrowserTelemetry {...settings} enabled />);
  expect(mocks.initialize).not.toHaveBeenCalled();
  first.unmount();
  dnt.mockReturnValue("0");
  mocks.initialize.mockResolvedValue({
    api: { setView: mocks.setView, pushEvent: mocks.pushEvent },
  });
  const view = render(<BrowserTelemetry {...settings} enabled />);
  await waitFor(() => expect(mocks.initialize).toHaveBeenCalledOnce());
  await waitFor(async () => {
    mocks.pathname = "/topics";
    view.rerender(<BrowserTelemetry {...settings} enabled />);
    expect(mocks.setView).toHaveBeenCalledWith({ name: "/topics" });
  });
  expect(mocks.pushEvent).toHaveBeenCalledWith("route_change", { url: "/topics" });
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
