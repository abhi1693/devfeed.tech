// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import type { DragEvent } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useBoardDrag } from "@/lib/use-board-drag";

afterEach(cleanup);
type Stage = "todo" | "done";
type Review = { reference: string; allowed: Stage[] };
const review: Review = { reference: "review-one", allowed: ["todo"] };
function event(target: HTMLElement = document.createElement("article")) {
  const data = new Map<string, string>();
  return {
    target,
    currentTarget: target,
    relatedTarget: null,
    preventDefault: vi.fn(),
    dataTransfer: {
      effectAllowed: "none",
      dropEffect: "none",
      setData: (key: string, value: string) => data.set(key, value),
      getData: (key: string) => data.get(key) ?? "",
    },
  } as unknown as DragEvent<HTMLElement>;
}
function setup() {
  const onDrop = vi.fn();
  const hook = renderHook(
    ({ disabled }) =>
      useBoardDrag<Review, Stage>({
        type: "application/x-review",
        disabled,
        getKey: (item) => item.reference,
        getTargets: (item) => item.allowed,
        onDrop,
      }),
    { initialProps: { disabled: false } },
  );
  return { ...hook, onDrop };
}
it("allows only the supplied transitions and checks the original MIME identity", () => {
  const { result, onDrop } = setup();
  const drag = event();
  act(() => result.current.cardProps(review).onDragStart(drag));
  expect(drag.dataTransfer.getData("application/x-review")).toBe(review.reference);
  expect(result.current.columnProps("todo").dropAllowed).toBe(true);
  expect(result.current.columnProps("done").dropAllowed).toBe(false);
  act(() => result.current.columnProps("done").onDrop(drag));
  expect(onDrop).not.toHaveBeenCalled();
  drag.dataTransfer.setData("application/x-review", "wrong");
  act(() => result.current.columnProps("todo").onDrop(drag));
  expect(onDrop).not.toHaveBeenCalled();
  drag.dataTransfer.setData("application/x-review", review.reference);
  act(() => result.current.columnProps("todo").onDragOver(drag));
  expect(result.current.columnProps("todo").dropActive).toBe(true);
  act(() => result.current.columnProps("todo").onDrop(drag));
  expect(onDrop).toHaveBeenCalledExactlyOnceWith(review, "todo");
  expect(result.current.item).toBeNull();
});
it.each(["button", "a"])("preserves interactive %s controls instead of starting a drag", (tag) => {
  const { result } = setup();
  const control = document.createElement(tag);
  const child = document.createElement("span");
  control.append(child);
  const drag = event(child);
  act(() => result.current.cardProps(review).onDragStart(drag));
  expect(drag.preventDefault).toHaveBeenCalled();
  expect(result.current.item).toBeNull();
});
it("rejects external drags and actions disabled after a drag starts", () => {
  const { result, rerender, onDrop } = setup();
  const drag = event();
  drag.dataTransfer.setData("application/x-review", review.reference);
  act(() => result.current.columnProps("todo").onDrop(drag));
  expect(onDrop).not.toHaveBeenCalled();
  act(() => result.current.cardProps(review).onDragStart(drag));
  rerender({ disabled: true });
  act(() => result.current.columnProps("todo").onDrop(drag));
  expect(onDrop).not.toHaveBeenCalled();
  expect(result.current.cardProps(review).draggable).toBe(false);
  act(() => result.current.cardProps(review).onDragEnd());
  expect(result.current.item).toBeNull();
});
it("keeps the target highlighted across child elements and clears it on exit", () => {
  const { result } = setup();
  const drag = event();
  act(() => result.current.cardProps(review).onDragStart(drag));
  act(() => result.current.columnProps("todo").onDragOver(drag));
  const child = document.createElement("span");
  drag.currentTarget.append(child);
  act(() => result.current.columnProps("todo").onDragLeave({ ...drag, relatedTarget: child }));
  expect(result.current.columnProps("todo").dropActive).toBe(true);
  act(() => result.current.columnProps("todo").onDragLeave(drag));
  expect(result.current.columnProps("todo").dropActive).toBe(false);
});
