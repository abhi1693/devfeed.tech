// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SnapshotStatus } from "@/components/molecules/snapshot-status";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function coverage(ageSeconds = 0, validForSeconds = 60) {
  return {
    status: "fresh" as const,
    generated_at: new Date(Date.now() - ageSeconds * 1000).toISOString(),
    coverage_expires_at: new Date(Date.now() + validForSeconds * 1000).toISOString(),
  };
}

it("shows the covered source snapshot rather than a recent request time", () => {
  const snapshot = coverage(120, 480);
  render(<SnapshotStatus requireCoverage snapshot={snapshot} />);
  expect(screen.getByRole("status").textContent).toMatch(/^Verified through/);
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(snapshot.generated_at);
});

it("keeps covered idle data fresh for its server-defined coverage window", async () => {
  vi.useFakeTimers();
  const snapshot = coverage(120, 480);
  render(<SnapshotStatus requireCoverage snapshot={snapshot} />);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toMatch(/^Verified through/);
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(snapshot.generated_at);
});

it("marks coverage stale as time passes without another response", async () => {
  vi.useFakeTimers();
  render(<SnapshotStatus requireCoverage snapshot={coverage()} />);
  expect(screen.getByRole("status").textContent).toMatch(/^Verified through/);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});

it("does not certify an expired coverage window from a newer displayed timestamp", () => {
  render(<SnapshotStatus requireCoverage snapshot={coverage(0, -1)} />);
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});

it.each([undefined, null, "invalid timestamp"])(
  "does not certify fresh data without valid coverage: %s",
  (coverage_expires_at) => {
    render(<SnapshotStatus requireCoverage snapshot={{ ...coverage(), coverage_expires_at }} />);
    expect(screen.getByRole("status").textContent).toContain("Catching up");
  },
);

it.each([undefined, null, "invalid timestamp"])(
  "does not invent a covered source snapshot: %s",
  (generated_at) => {
    render(<SnapshotStatus requireCoverage snapshot={{ ...coverage(), generated_at }} />);
    expect(screen.getByRole("status").textContent).toBe("Catching up · Coverage not yet available");
    expect(screen.queryByRole("time")).toBeNull();
  },
);

it("retains the age guard for older snapshots without a coverage checkpoint", () => {
  render(
    <SnapshotStatus
      snapshot={{ status: "fresh", generated_at: new Date(Date.now() - 120_000).toISOString() }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});

it.each([
  ["stale", "Stale data"],
  ["catching_up", "Catching up"],
] as const)("preserves the server's %s status inside a valid coverage window", (status, label) => {
  render(<SnapshotStatus requireCoverage snapshot={{ ...coverage(), status }} />);
  expect(screen.getByRole("status").textContent).toContain(label);
});

it("keeps initial coverage incomplete rather than guessing freshness from page activity", async () => {
  vi.useFakeTimers();
  render(<SnapshotStatus requireCoverage snapshot={{ status: "catching_up" }} />);
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toBe("Catching up · Coverage not yet available");
});

it("ages incomplete coverage without discarding its last completed result", async () => {
  vi.useFakeTimers();
  const snapshot = { ...coverage(), status: "catching_up" as const };
  render(<SnapshotStatus requireCoverage snapshot={snapshot} />);
  expect(screen.getByRole("status").textContent).toContain("Catching up");
  await act(() => vi.advanceTimersByTimeAsync(90_000));
  expect(screen.getByRole("status").textContent).toContain("Stale data");
  expect(screen.getByRole("time").getAttribute("datetime")).toBe(snapshot.generated_at);
});

it("keeps unfinished catalog age visible beside a covered result", () => {
  render(
    <SnapshotStatus
      snapshot={{
        ...coverage(),
        status: "catching_up",
        catalog_lag_seconds: 3600,
        lag_seconds: 2,
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("Catalog changes pending for 60 min");
});

it("preserves historical freshness and its Updated label without reconciliation metadata", () => {
  render(<SnapshotStatus snapshot={{ status: "fresh", generated_at: new Date().toISOString() }} />);
  expect(screen.getByRole("status").textContent).toMatch(/^Updated/);
  expect(screen.getByRole("status").textContent).not.toContain("Catching up");
  expect(screen.getByRole("status").textContent).not.toContain("Verified through");
});

it("expires current coverage at its deadline between regular age updates", async () => {
  vi.useFakeTimers();
  render(<SnapshotStatus requireCoverage snapshot={coverage(0, 5)} />);
  expect(screen.getByRole("status").textContent).toMatch(/^Verified through/);
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(screen.getByRole("status").textContent).toContain("Stale data");
});
