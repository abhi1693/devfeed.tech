// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { usePipelineMotion } from "@/lib/use-pipeline-motion";

type Card = { id: string; stage: string; hidden?: boolean; row?: number };
const stages = ["ingested", "analysis", "published"];
const animations: { element: HTMLElement; cancel: ReturnType<typeof vi.fn> }[] = [];
const animate = vi.fn(function (this: HTMLElement) {
  const animation = { element: this, cancel: vi.fn(), onfinish: null, oncancel: null };
  animations.push(animation);
  return animation as unknown as Animation;
});
let reduced = false;
let mediaChange: (() => void) | undefined;

function Board({
  cards,
  layoutKey = "",
  offset = 0,
}: {
  cards: Card[];
  layoutKey?: string;
  offset?: number;
}) {
  const ref = usePipelineMotion(layoutKey);
  return (
    <div ref={ref} data-testid="region" data-offset={offset} tabIndex={0}>
      {stages.map((stage) => (
        <section key={stage}>
          {cards
            .filter((card) => card.stage === stage)
            .map((card) => (
              <article
                key={card.id}
                data-pipeline-article-id={card.id}
                data-pipeline-stage={card.stage}
                data-row={card.row ?? 0}
                hidden={card.hidden}
              />
            ))}
        </section>
      ))}
    </div>
  );
}

beforeEach(() => {
  delete document.documentElement.dataset.reduceMotion;
  reduced = false;
  mediaChange = undefined;
  animations.length = 0;
  animate.mockClear();
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return reduced;
    },
    addEventListener: (_event: string, listener: () => void) => (mediaChange = listener),
    removeEventListener: vi.fn(),
  }));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    if (this.dataset.testid === "region") return new DOMRect(100, 50, 900, 500);
    const root = this.closest<HTMLElement>("[data-testid='region']");
    if (!root || this.hidden) return new DOMRect();
    return new DOMRect(
      100 +
        stages.indexOf(this.dataset.pipelineStage!) * 300 +
        Number(root.dataset.offset) -
        root.scrollLeft,
      80 + Number(this.dataset.row) * 120 - root.scrollTop,
      260,
      100,
    );
  });
  Object.defineProperty(HTMLElement.prototype, "animate", {
    configurable: true,
    writable: true,
    value: animate,
  });
});

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.reduceMotion;
  vi.unstubAllGlobals();
});

it("animates the same article forward and backward across reparented lanes", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  expect(animate).not.toHaveBeenCalled();
  rerender(<Board cards={[{ id: "one", stage: "analysis", row: 1 }]} />);
  expect(animate).toHaveBeenLastCalledWith(
    [
      { transform: "translate(-300px, -120px)", zIndex: 20 },
      { transform: "translate(0, 0)", zIndex: 20 },
    ],
    { duration: 360, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
  );
  expect(animations[0].element.dataset.pipelineStage).toBe("analysis");
  rerender(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  expect(animate).toHaveBeenLastCalledWith(
    [
      { transform: "translate(300px, 120px)", zIndex: 20 },
      { transform: "translate(0, 0)", zIndex: 20 },
    ],
    expect.anything(),
  );
  expect(animations[0].cancel).toHaveBeenCalledOnce();
});

it("does not animate first appearances or new cards", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  rerender(
    <Board
      cards={[
        { id: "one", stage: "ingested" },
        { id: "two", stage: "published" },
      ]}
    />,
  );
  expect(animate).not.toHaveBeenCalled();
});

it("animates an existing card vertically when running-first ordering changes within its lane", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "analysis", row: 2 }]} />);
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animate).toHaveBeenLastCalledWith(
    [
      { transform: "translate(0px, 240px)", zIndex: 20 },
      { transform: "translate(0, 0)", zIndex: 20 },
    ],
    expect.anything(),
  );
});

it("uses board content coordinates so horizontal scrolling neither triggers nor distorts a move", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  const region = screen.getByTestId("region");
  region.scrollLeft = 200;
  region.scrollTop = 40;
  region.focus();
  rerender(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  expect(animate).not.toHaveBeenCalled();
  region.scrollLeft = 500;
  rerender(<Board cards={[{ id: "one", stage: "published" }]} />);
  expect(animate).toHaveBeenLastCalledWith(
    [
      { transform: "translate(-600px, 0px)", zIndex: 20 },
      { transform: "translate(0, 0)", zIndex: 20 },
    ],
    expect.anything(),
  );
  expect(region.scrollLeft).toBe(500);
  expect(region.scrollTop).toBe(40);
  expect(document.activeElement).toBe(region);
});

it("skips zero-sized hidden mobile cards in either the old or new lane", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested", hidden: true }]} />);
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animate).not.toHaveBeenCalled();
  rerender(<Board cards={[{ id: "one", stage: "published", hidden: true }]} />);
  expect(animate).not.toHaveBeenCalled();
});

it("disables movement for reduced motion and cancels immediately when the preference changes", () => {
  reduced = true;
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animate).not.toHaveBeenCalled();
  reduced = false;
  act(() => mediaChange?.());
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  rerender(<Board cards={[{ id: "one", stage: "published" }]} />);
  expect(animate).toHaveBeenCalledOnce();
  reduced = true;
  act(() => mediaChange?.());
  expect(animations[0].cancel).toHaveBeenCalledOnce();
});

it("also honors the admin Reduce motion setting and cancels when it is enabled", async () => {
  document.documentElement.dataset.reduceMotion = "true";
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animate).not.toHaveBeenCalled();
  await act(async () => {
    delete document.documentElement.dataset.reduceMotion;
    await Promise.resolve();
  });
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  rerender(<Board cards={[{ id: "one", stage: "published" }]} />);
  expect(animate).toHaveBeenCalledOnce();
  await act(async () => {
    document.documentElement.dataset.reduceMotion = "true";
    await Promise.resolve();
  });
  expect(animations[0].cancel).toHaveBeenCalledOnce();
});

it("cancels previous animations on the next update and on unmount", () => {
  const { rerender, unmount } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animations[0].cancel).not.toHaveBeenCalled();
  rerender(<Board cards={[{ id: "one", stage: "analysis" }]} />);
  expect(animations[0].cancel).toHaveBeenCalledOnce();
  expect(animate).toHaveBeenCalledOnce();
  rerender(<Board cards={[{ id: "one", stage: "published" }]} />);
  unmount();
  expect(animations[1].cancel).toHaveBeenCalledOnce();
});

it("drops stale geometry after a responsive layout resize", () => {
  const { rerender } = render(<Board cards={[{ id: "one", stage: "ingested" }]} />);
  act(() => window.dispatchEvent(new Event("resize")));
  rerender(<Board cards={[{ id: "one", stage: "published" }]} />);
  expect(animate).not.toHaveBeenCalled();
});

it("rebaselines collapsed or hidden columns and resumes animation on the next data refresh", () => {
  const { rerender } = render(
    <Board cards={[{ id: "one", stage: "analysis", row: 1 }]} layoutKey="all|expanded" />,
  );
  rerender(
    <Board
      cards={[{ id: "one", stage: "analysis" }]}
      layoutKey="all|ingested-collapsed"
      offset={-240}
    />,
  );
  expect(animate).not.toHaveBeenCalled();
  rerender(
    <Board
      cards={[{ id: "one", stage: "analysis", row: 2 }]}
      layoutKey="analysis,published|"
      offset={-300}
    />,
  );
  expect(animate).not.toHaveBeenCalled();
  rerender(
    <Board
      cards={[{ id: "one", stage: "published" }]}
      layoutKey="analysis,published|"
      offset={-300}
    />,
  );
  expect(animate).toHaveBeenLastCalledWith(
    [
      { transform: "translate(-300px, 240px)", zIndex: 20 },
      { transform: "translate(0, 0)", zIndex: 20 },
    ],
    expect.anything(),
  );
  rerender(<Board cards={[{ id: "one", stage: "published" }]} layoutKey="all|expanded" />);
  expect(animations[0].cancel).toHaveBeenCalledOnce();
  expect(animate).toHaveBeenCalledOnce();
});
