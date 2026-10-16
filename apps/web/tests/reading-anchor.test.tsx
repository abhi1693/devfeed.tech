// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useReadingAnchor } from "@/components/use-reading-anchor";

let anchor: ReturnType<typeof useReadingAnchor>;
function Feed() {
  const { ref, beforeDisplay, afterDisplay } = useReadingAnchor(".article-card");
  useLayoutEffect(() => {
    anchor = { ref, beforeDisplay, afterDisplay };
  });
  return (
    <div ref={ref}>
      <article className="article-card">
        <a href="#article-reading">Article title</a>
        <button type="button">Bookmark article</button>
      </article>
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  (document.scrollingElement ?? document.documentElement).scrollTop = 0;
});

it.each(["link", "button"] as const)(
  "preserves the focused %s when a responsive hero changes its position within the same card",
  (role) => {
    render(<Feed />);
    const card = document.querySelector<HTMLElement>(".article-card")!;
    const focused = screen.getByRole(role);
    const scroller = document.scrollingElement ?? document.documentElement;
    vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1280, 900));
    vi.spyOn(card, "getBoundingClientRect").mockImplementation(
      () => new DOMRect(0, 100 - scroller.scrollTop, 300, 360),
    );
    let heroHeight = 120;
    vi.spyOn(focused, "getBoundingClientRect").mockImplementation(
      () => new DOMRect(0, 100 + heroHeight - scroller.scrollTop, 250, 30),
    );
    focused.focus();
    const originalTop = focused.getBoundingClientRect().top;
    act(() => anchor.beforeDisplay());
    heroHeight += 16.625;
    act(() => anchor.afterDisplay());
    expect(document.activeElement).toBe(focused);
    expect(scroller.scrollTop).toBe(16.625);
    expect(focused.getBoundingClientRect().top).toBe(originalTop);
  },
);

it("preserves the first visible article when no control inside a result has focus", () => {
  render(<Feed />);
  const card = document.querySelector<HTMLElement>(".article-card")!;
  const scroller = document.scrollingElement ?? document.documentElement;
  vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1280, 900));
  let position = 100;
  vi.spyOn(card, "getBoundingClientRect").mockImplementation(
    () => new DOMRect(0, position - scroller.scrollTop, 300, 360),
  );
  act(() => anchor.beforeDisplay());
  position += 180;
  act(() => anchor.afterDisplay());
  expect(scroller.scrollTop).toBe(180);
  expect(card.getBoundingClientRect().top).toBe(100);
});
