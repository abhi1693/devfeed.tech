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

it("keeps a recently verified idle board fresh while displaying its last actual update", async () => {
  vi.useFakeTimers();
  const checkedAt = new Date().toISOString();
  const generatedAt = new Date(Date.now() - 86_400_000).toISOString();
  render(
    <SnapshotStatus
      snapshot={{ status: "fresh", generated_at: generatedAt }}
      checkedAt={checkedAt}
    />,
  );
  expect(screen.getByRole("status").textContent).toMatch(/^Updated/);
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(generatedAt);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toContain("Stale data");
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(generatedAt);
});

it("marks an old verification stale even when the displayed data timestamp is newer", () => {
  render(
    <SnapshotStatus
      snapshot={{ status: "fresh", generated_at: new Date().toISOString() }}
      checkedAt={new Date(Date.now() - 120_000).toISOString()}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});

it.each([undefined, "invalid timestamp"])(
  "retains the report age guard when verification is unavailable: %s",
  (checkedAt) => {
    render(
      <SnapshotStatus
        snapshot={{ status: "fresh", generated_at: new Date(Date.now() - 120_000).toISOString() }}
        checkedAt={checkedAt}
      />,
    );
    expect(screen.getByRole("status").textContent).toContain("Stale data");
  },
);

it.each([
  ["stale", "Stale data"],
  ["rebuilding", "Rebuilding saved data"],
  ["refreshing", "Updating saved data"],
] as const)("preserves the server's %s status after a recent response", (status, label) => {
  render(
    <SnapshotStatus
      snapshot={{ status, generated_at: new Date().toISOString() }}
      checkedAt={new Date().toISOString()}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain(label);
});
