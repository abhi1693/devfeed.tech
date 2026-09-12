// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { WorkersOverview, WorkerDetails, QueuesOverview } from "@/components/organisms/workers";
import { adminWorkerGet, adminWorkersSnapshot } from "@/lib/api/generated/admin";
import type { WorkerOut, WorkersSnapshot } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/api/generated/admin", () => ({
  adminWorkerGet: vi.fn(),
  adminWorkersSnapshot: vi.fn(),
}));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
vi.mock("@/components/organisms/job-logs", () => ({
  JobLogs: ({ kind, id }: { kind: string; id: string }) => (
    <div data-testid="logs">
      {kind}/{id}
    </div>
  ),
}));
const worker: WorkerOut = {
  name: "ai-worker-1",
  role: "ai",
  queues: ["analysis", "relationships"],
  state: "busy",
  registered: true,
  hostname: "host-1",
  pid: 12,
  started_at: "2026-09-10T18:00:00Z",
  last_heartbeat: "2026-09-10T18:01:00Z",
  uptime_seconds: 60,
  heartbeat_age_seconds: 1,
  registration_ttl_seconds: 90,
  completed_executions: 42,
  failed_executions: 2,
  working_seconds: 30,
  current_job: {
    rq_id: "rq-1",
    id: "job-1",
    kind: "research-verification",
    proposal_id: "proposal-1",
    target_name: "React",
    status: "running",
    elapsed_seconds: 5,
  },
};
const snapshot: WorkersSnapshot = {
  generated_at: "2026-09-10T18:01:01Z",
  workers: [worker],
  ai_cooldown_seconds: 0,
  queues: [
    {
      name: "analysis",
      registered_workers: 1,
      busy_workers: 1,
      idle_workers: 0,
      suspended_workers: 0,
      dispatched: 5,
      queued: 6,
      running: 1,
      failed: 0,
      succeeded: 20,
      review_required: 8,
      oldest_queued_at: "2026-09-10T17:00:00Z",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(adminWorkerGet).mockResolvedValue(worker);
  vi.mocked(adminWorkersSnapshot).mockResolvedValue(snapshot);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("shows worker activity and links verification to its topic run", async () => {
  render(<WorkersOverview />);
  const table = await screen.findByRole("table", { name: "Workers" });
  expect(within(table).getByRole("link", { name: "ai-worker-1" }).getAttribute("href")).toBe(
    "/workers/ai-worker-1",
  );
  expect(within(table).getByRole("link", { name: "React" }).getAttribute("href")).toBe(
    "/jobs/analysis/topics/job-1",
  );
  expect(within(table).getByText("42")).toBeDefined();
  expect(within(table).getByText("2")).toBeDefined();
  expect(within(table).queryByText("Heartbeat age")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Search workers" }), {
    target: { value: "missing" },
  });
  expect(screen.getByText("No workers match these filters.")).toBeDefined();
});

it("distinguishes production registrations sharing a prefix and preserves their links", async () => {
  const registrations: WorkerOut[] = [
    {
      ...worker,
      name: "devfeed-ai-worker-65f874c96d-abc12-0123456789abcdef01234567a1b2c3d4",
      hostname: "devfeed-ai-worker-65f874c96d-abc12",
    },
    {
      ...worker,
      name: "devfeed-ai-worker-65f874c96d-def34-0123456789abcdef01234567e5f6a7b8",
      hostname: "devfeed-ai-worker-65f874c96d-def34",
    },
    {
      ...worker,
      role: "background",
      name: "devfeed-worker-65f874c96d-ghi56-0123456789abcdef012345671234abcd",
      hostname: "devfeed-worker-65f874c96d-ghi56",
    },
    {
      ...worker,
      role: "solver",
      name: "devfeed-solver-worker-65f874c96d-jkl78-0123456789abcdef012345675678efab",
      hostname: null,
    },
  ];
  vi.mocked(adminWorkersSnapshot).mockResolvedValue({ ...snapshot, workers: registrations });
  render(<WorkersOverview />);
  const table = await screen.findByRole("table", { name: "Workers" });
  const labels = ["AI · a1b2c3d4", "AI · e5f6a7b8", "Background · 1234abcd", "Solver · 5678efab"];
  registrations.forEach((registration, index) => {
    const link = within(table).getByRole("link", { name: labels[index] });
    expect(link.getAttribute("href")).toBe(`/workers/${encodeURIComponent(registration.name)}`);
    expect(link.getAttribute("title")).toBe(registration.name);
    if (registration.hostname) expect(within(table).getByText(registration.hostname)).toBeDefined();
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Search workers" }), {
    target: { value: "e5f6a7b8" },
  });
  expect(within(table).getByRole("link", { name: labels[1] })).toBeDefined();
  expect(within(table).queryByRole("link", { name: labels[0] })).toBeNull();
});

it("uses the same registration label on worker details", async () => {
  const registration = {
    ...worker,
    name: "devfeed-ai-worker-65f874c96d-abc12-0123456789abcdef01234567a1b2c3d4",
  };
  vi.mocked(adminWorkerGet).mockResolvedValue(registration);
  render(<WorkerDetails name={registration.name} />);
  expect(await screen.findByRole("heading", { name: "AI · a1b2c3d4" })).toBeDefined();
  expect(screen.getByText(registration.name)).toBeDefined();
});

it("keeps successful data visible with an explicit stale warning on polling failure", async () => {
  vi.useFakeTimers();
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<WorkersOverview />);
  });
  vi.mocked(adminWorkersSnapshot).mockRejectedValueOnce(new ApiError(503));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(screen.getByRole("alert").textContent).toContain("last successful snapshot");
  expect(screen.getByRole("table", { name: "Workers" })).toBeDefined();
  view.unmount();
  const calls = vi.mocked(adminWorkersSnapshot).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(adminWorkersSnapshot).toHaveBeenCalledTimes(calls);
});

it("opens the verification logs and proposal without implying approval", async () => {
  render(<WorkerDetails name="ai-worker-1" />);
  await screen.findByText("Research verification · 5s");
  expect(screen.getByTestId("logs").textContent).toBe("topic-analysis/job-1");
  expect(screen.getByRole("link", { name: "Open topic proposal" }).getAttribute("href")).toBe(
    "/taxonomy/topics/proposals/proposal-1",
  );
  expect(screen.getByRole("link", { name: "Job logs" }).getAttribute("href")).toBe(
    "/jobs/analysis/topics/job-1/logs",
  );
});

it("explains a worker disappearing after a restart", async () => {
  vi.mocked(adminWorkerGet).mockRejectedValue(
    new ApiError(404, "Worker is no longer registered; it may have stopped or restarted"),
  );
  render(<WorkerDetails name="gone" />);
  expect((await screen.findByRole("alert")).textContent).toContain("stopped or restarted");
  expect(screen.queryByText("No current job.")).toBeNull();
});

it("shows review-required completions separately from failures and warns about missing capacity", async () => {
  vi.mocked(adminWorkersSnapshot).mockResolvedValue({
    ...snapshot,
    ai_cooldown_seconds: 120,
    queues: [{ ...snapshot.queues[0], registered_workers: 0 }],
  });
  render(<QueuesOverview />);
  await screen.findByText("Waiting work has no registered workers.");
  expect(screen.getByText("AI provider cooldown: 2m 0s remaining.")).toBeDefined();
  expect(screen.getByText("Review required").parentElement?.textContent).toBe("Review required8");
  expect(screen.getByText("Failed").parentElement?.textContent).toBe("Failed0");
});
