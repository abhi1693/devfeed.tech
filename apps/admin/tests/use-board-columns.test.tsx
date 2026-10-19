// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useBoardColumns } from "@/lib/use-board-columns";
const pipelineStages = [
  "ingested",
  "analysis",
  "images",
  "ready",
  "published",
  "rejected",
] as const;
const pipelineColumnsStorageKey = "devfeed:test-board-columns";
function usePipelineColumns() {
  return useBoardColumns(pipelineColumnsStorageKey, pipelineStages);
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
  cleanup();
});

function Probe() {
  const { visible, collapsed } = usePipelineColumns();
  return (
    <div>
      <output data-testid="visible">{visible.join(",")}</output>
      <output data-testid="collapsed">{collapsed.join(",")}</output>
    </div>
  );
}

it("keeps column preferences independent between boards", () => {
  const first = renderHook(() => useBoardColumns("first-board", pipelineStages));
  const second = renderHook(() => useBoardColumns("second-board", pipelineStages));
  act(() => {
    first.result.current.setVisible("images", false);
    first.result.current.toggleCollapsed("analysis");
  });
  expect(first.result.current.visible).not.toContain("images");
  expect(first.result.current.collapsed).toEqual(["analysis"]);
  expect(second.result.current.visible).toEqual(pipelineStages);
  expect(second.result.current.collapsed).toEqual([]);
});

it("hydrates the all-visible server fallback before restoring browser preferences", async () => {
  localStorage.setItem(
    pipelineColumnsStorageKey,
    JSON.stringify({ hidden: ["images"], collapsed: ["analysis"] }),
  );
  const read = vi.spyOn(Storage.prototype, "getItem");
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  const html = renderToString(<Probe />);
  expect(read).not.toHaveBeenCalled();
  expect(html).toContain(pipelineStages.join(","));
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.append(container);
  render(<Probe />, { container, hydrate: true });
  await waitFor(() =>
    expect(screen.getByTestId("visible").textContent).toBe(
      pipelineStages.filter((stage) => stage !== "images").join(","),
    ),
  );
  expect(screen.getByTestId("collapsed").textContent).toBe("analysis");
  expect(errors).not.toHaveBeenCalled();
});

it.each([
  "invalid JSON",
  "null",
  "[]",
  "5",
  '{"hidden":"images","collapsed":[]}',
  '{"hidden":[],"collapsed":null}',
])("uses defaults for malformed stored preferences: %s", (stored) => {
  localStorage.setItem(pipelineColumnsStorageKey, stored);
  const { result } = renderHook(() => usePipelineColumns());
  expect(result.current.visible).toEqual(pipelineStages);
  expect(result.current.collapsed).toEqual([]);
});

it("filters unknown stages and duplicates while preserving canonical column order", () => {
  localStorage.setItem(
    pipelineColumnsStorageKey,
    JSON.stringify({
      hidden: ["images", "images", "unknown", null, 42],
      collapsed: ["ready", "analysis", "analysis", "unknown", {}],
    }),
  );
  const { result } = renderHook(() => usePipelineColumns());
  expect(result.current.visible).toEqual(pipelineStages.filter((stage) => stage !== "images"));
  expect(result.current.collapsed).toEqual(["analysis", "ready"]);
});

it("recovers all columns from an all-hidden preference without losing collapse choices", () => {
  localStorage.setItem(
    pipelineColumnsStorageKey,
    JSON.stringify({ hidden: pipelineStages, collapsed: ["analysis"] }),
  );
  const { result } = renderHook(() => usePipelineColumns());
  expect(result.current.visible).toEqual(pipelineStages);
  expect(result.current.collapsed).toEqual(["analysis"]);
});

it("guards the last visible column even when several changes share a render", () => {
  const { result } = renderHook(() => usePipelineColumns());
  act(() => {
    for (const stage of pipelineStages) result.current.setVisible(stage, false);
  });
  expect(result.current.visible).toEqual(["rejected"]);
  expect(JSON.parse(localStorage.getItem(pipelineColumnsStorageKey)!)).toEqual({
    hidden: pipelineStages.slice(0, -1),
    collapsed: [],
  });
  act(() => {
    result.current.setVisible("ingested", true);
    result.current.setVisible("rejected", false);
  });
  expect(result.current.visible).toEqual(["ingested"]);
});

it("restores preferences on remount and keeps hidden-column collapse independent", () => {
  const first = renderHook(() => usePipelineColumns());
  act(() => {
    first.result.current.toggleCollapsed("analysis");
    first.result.current.setVisible("analysis", false);
    first.result.current.setVisible("images", false);
  });
  first.unmount();
  const { result } = renderHook(() => usePipelineColumns());
  expect(result.current.visible).toEqual(
    pipelineStages.filter((stage) => stage !== "analysis" && stage !== "images"),
  );
  expect(result.current.collapsed).toEqual(["analysis"]);
  act(() => result.current.showAll());
  expect(result.current.visible).toEqual(pipelineStages);
  expect(result.current.collapsed).toEqual(["analysis"]);
  act(() => result.current.toggleCollapsed("analysis"));
  expect(result.current.collapsed).toEqual([]);
  expect(JSON.parse(localStorage.getItem(pipelineColumnsStorageKey)!)).toEqual({
    hidden: [],
    collapsed: [],
  });
});

it.each(["clear", "remove"])("synchronizes local consumers and cross-tab %s", (operation) => {
  const first = renderHook(() => usePipelineColumns());
  const second = renderHook(() => usePipelineColumns());
  act(() => first.result.current.setVisible("images", false));
  expect(second.result.current.visible).not.toContain("images");
  const external = JSON.stringify({ hidden: ["published"], collapsed: ["images"] });
  act(() => {
    localStorage.setItem(pipelineColumnsStorageKey, external);
    window.dispatchEvent(
      new StorageEvent("storage", { key: pipelineColumnsStorageKey, newValue: external }),
    );
  });
  expect(first.result.current.visible).not.toContain("published");
  expect(first.result.current.visible).toContain("images");
  expect(second.result.current.collapsed).toEqual(["images"]);
  act(() =>
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", newValue: null })),
  );
  expect(second.result.current.collapsed).toEqual(["images"]);
  act(() => {
    if (operation === "clear") localStorage.clear();
    else localStorage.removeItem(pipelineColumnsStorageKey);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: operation === "clear" ? null : pipelineColumnsStorageKey,
        newValue: null,
      }),
    );
  });
  expect(first.result.current.visible).toEqual(pipelineStages);
  expect(second.result.current.collapsed).toEqual([]);
});

it.each(["read", "write", "silent"])(
  "keeps preferences usable when storage fails: %s",
  (failure) => {
    if (failure === "silent") vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {});
    else
      vi.spyOn(Storage.prototype, failure === "read" ? "getItem" : "setItem").mockImplementation(
        () => {
          throw new DOMException("Storage unavailable", "SecurityError");
        },
      );
    const first = renderHook(() => usePipelineColumns());
    expect(first.result.current.visible).toEqual(pipelineStages);
    act(() => {
      first.result.current.setVisible("images", false);
      first.result.current.toggleCollapsed("analysis");
    });
    expect(first.result.current.visible).not.toContain("images");
    expect(first.result.current.collapsed).toEqual(["analysis"]);
    first.unmount();
    const { result } = renderHook(() => usePipelineColumns());
    expect(result.current.visible).not.toContain("images");
    expect(result.current.collapsed).toEqual(["analysis"]);
    act(() => result.current.showAll());
    expect(result.current.visible).toEqual(pipelineStages);
    expect(result.current.collapsed).toEqual(["analysis"]);
  },
);
