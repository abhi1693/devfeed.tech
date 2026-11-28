// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { animateReader } from "@/lib/reader-motion";
import { ReaderDisclosure } from "@/components/reader-disclosure";
import { LoadingReveal } from "@/components/loading-reveal";
import { SaveFeedback } from "@/components/motion-icon";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(Element.prototype, "animate");
  vi.useRealTimers();
});
function animations() {
  const effects: {
    finished: Promise<void>;
    finish: () => void;
    cancel: ReturnType<typeof vi.fn>;
  }[] = [];
  const animate = vi.fn(() => {
    let finish!: () => void;
    const finished = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const effect = { finished, finish, cancel: vi.fn() };
    effects.push(effect);
    return effect as unknown as Animation;
  });
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    writable: true,
    value: () => {},
  });
  vi.spyOn(Element.prototype, "animate").mockImplementation(animate);
  return { animate, effects };
}
it("skips animations when motion is reduced or the browser lacks support", () => {
  const element = document.createElement("div");
  expect(animateReader(element, [])).toBeNull();
  const { animate } = animations();
  vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
  expect(animateReader(element, [])).toBeNull();
  expect(animate).not.toHaveBeenCalled();
});
it("keeps disclosures open through their closing animation and cancels on unmount", async () => {
  const { effects } = animations();
  const view = render(<ReaderDisclosure title="About Python">Description</ReaderDisclosure>);
  const summary = screen.getByText("About Python");
  const details = summary.closest("details")!;
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  await act(async () => effects[0].finish());
  fireEvent.click(summary);
  expect(details.open).toBe(true);
  await act(async () => effects[1].finish());
  expect(details.open).toBe(false);
  fireEvent.click(summary);
  view.unmount();
  expect(effects[2].cancel).toHaveBeenCalledOnce();
});
it("removes the loading announcement immediately while fading out the placeholder", async () => {
  vi.useFakeTimers();
  const fallback = <div role="status">Loading</div>;
  const view = render(<LoadingReveal loading fallback={fallback} />);
  expect(screen.getByRole("status")).toBeTruthy();
  view.rerender(
    <LoadingReveal loading={false} fallback={fallback}>
      <button>Result</button>
    </LoadingReveal>,
  );
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("button", { name: "Result" })).toBeTruthy();
  expect(screen.getByText("Loading").parentElement?.hasAttribute("inert")).toBe(true);
  await act(async () => vi.advanceTimersByTime(180));
  expect(screen.queryByText("Loading")).toBeNull();
});
it("shows a check only for confirmed saves", () => {
  const view = render(<SaveFeedback busy={false} saved={false} />);
  expect(view.container.querySelector("svg")).toBeNull();
  view.rerender(<SaveFeedback busy saved={false} />);
  expect(view.container.querySelector(".settings-spinner")).toBeTruthy();
  view.rerender(<SaveFeedback busy={false} saved={false} />);
  expect(view.container.querySelector("svg")).toBeNull();
  view.rerender(<SaveFeedback busy={false} saved />);
  expect(view.container.querySelector(".lucide-check")).toBeTruthy();
});
it("preserves the tab indicator origin across a route remount", async () => {
  const { ReaderTabs } = await import("@/components/reader-tabs");
  const { animate } = animations();
  vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.textContent === "News" ? 80 : 0;
  });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(70);
  const tabs = (selected: string) => (
    <ReaderTabs scope="feed" selected={selected}>
      <a href="#all" aria-current={selected === "all" ? "page" : undefined}>
        All
      </a>
      <a href="#news" aria-current={selected === "news" ? "page" : undefined}>
        News
      </a>
    </ReaderTabs>
  );
  const view = render(tabs("all"));
  fireEvent.click(screen.getByRole("link", { name: "News" }));
  view.unmount();
  render(tabs("news"));
  expect(animate).toHaveBeenCalledWith(
    [
      { transform: "translateX(0px)", width: "70px" },
      { transform: "translateX(80px)", width: "70px" },
    ],
    expect.objectContaining({ duration: 180 }),
  );
});
