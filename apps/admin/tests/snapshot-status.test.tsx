// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SnapshotStatus } from "@/components/molecules/snapshot-status";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("exposes stale and rebuilding saved data without hiding it", () => {
  const snapshot = { status: "stale" as const, generated_at: new Date().toISOString() };
  const view = render(<SnapshotStatus snapshot={snapshot} />);
  expect(screen.getByRole("status").textContent).toContain("Stale data");
  view.rerender(<SnapshotStatus snapshot={{ ...snapshot, status: "rebuilding" }} />);
  expect(screen.getByRole("status").textContent).toContain("Rebuilding saved data");
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(snapshot.generated_at);
});

it("marks a loaded view stale as time passes without another response", async () => {
  vi.useFakeTimers();
  render(<SnapshotStatus snapshot={{ status: "fresh", generated_at: new Date().toISOString() }} />);
  expect(screen.getByRole("status").textContent).not.toContain("Stale data");
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});
