// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAdmin } from "./render-admin";
import { RefreshSettings, saveRefresh } from "./refresh-settings";
import { item, board, detail } from "./fixtures/source-pipeline";
import { SourcePipeline } from "@/components/organisms/source-pipeline";
import { SourceReadiness } from "@/components/organisms/source-readiness";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceWorkflow } from "@/components/organisms/resource-workflow";
import {
  advanceSourcePipeline,
  getSourcePipeline,
  getSourcePipelineColumn,
  getSourcePipelineDetail,
  retrySourcePipeline,
  sourceColumnsStorageKey,
  sourcePipelineHost,
  sourcePipelineJobHref,
  sourcePipelineParams,
  sourceRetryLabel,
  type SourcePipelineBoard,
} from "@/lib/source-pipeline";
import { getRecord, listRecords } from "@/lib/resource-api";
import {
  adminSourcePipeline,
  adminSourcePipelineAdvance,
  adminSourcePipelineColumn,
  adminSourcePipelineDetail,
  adminSourcePipelineRetry,
  adminSourceReview,
  adminSourceFetch,
} from "@/lib/api/generated/admin";

const navigation = vi.hoisted(() => ({ query: "view=pipeline" }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(navigation.query),
  usePathname: () => "/content/sources",
}));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
vi.mock("@/lib/source-pipeline", async (original) => ({
  ...(await original<typeof import("@/lib/source-pipeline")>()),
  getSourcePipeline: vi.fn(),
  getSourcePipelineColumn: vi.fn(),
  getSourcePipelineDetail: vi.fn(),
  advanceSourcePipeline: vi.fn(),
  retrySourcePipeline: vi.fn(),
}));
vi.mock("@/lib/resource-api", async (original) => ({
  ...(await original<typeof import("@/lib/resource-api")>()),
  listRecords: vi.fn(),
  getRecord: vi.fn(),
}));
vi.mock("@/lib/api/generated/admin", async (original) => ({
  ...(await original<typeof import("@/lib/api/generated/admin")>()),
  adminSourcePipeline: vi.fn(),
  adminSourcePipelineColumn: vi.fn(),
  adminSourcePipelineDetail: vi.fn(),
  adminSourcePipelineAdvance: vi.fn(),
  adminSourcePipelineRetry: vi.fn(),
  adminSourceReview: vi.fn(),
  adminSourceFetch: vi.fn(),
}));

function transfer() {
  const values = new Map<string, string>();
  return {
    setData: (key: string, value: string) => values.set(key, value),
    getData: (key: string) => values.get(key) ?? "",
    effectAllowed: "all",
    dropEffect: "none",
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  navigation.query = "view=pipeline";
  vi.stubGlobal("innerWidth", 1440);
  vi.mocked(getSourcePipeline).mockResolvedValue(board());
  vi.mocked(getSourcePipelineDetail).mockResolvedValue(detail());
  vi.mocked(advanceSourcePipeline).mockResolvedValue(detail());
  vi.mocked(retrySourcePipeline).mockResolvedValue(detail());
  vi.mocked(listRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.mocked(getRecord).mockResolvedValue({
    id: item.id,
    name: item.name,
    approval_status: "pending",
    enabled: false,
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("forwards only bounded source pipeline filters", () => {
  expect(
    sourcePipelineParams(
      "view=pipeline&stage=active&limit=500&cursor=999&offset=100&q=Python&source_type=publisher&needs_attention=true&terminal_since=2026-10-12T00:00:00Z",
    ),
  ).toEqual({
    limit: 20,
    q: "Python",
    source_type: "publisher",
    needs_attention: true,
    terminal_since: "2026-10-12T00:00:00Z",
  });
  expect(sourcePipelineParams("source_type=unknown&needs_attention=false&sort=name")).toEqual({
    limit: 20,
  });
});

it("uses generated clients with source identity, snapshot, job baseline and CSRF", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/source-pipeline")>("@/lib/source-pipeline");
  const signal = new AbortController().signal;
  const retry = { stage: "profile" as const, job_id: "old-profile-job" };
  await actual.getSourcePipeline("q=Python", signal);
  await actual.getSourcePipelineColumn("profile", "source_type=aggregator", 20, signal);
  await actual.getSourcePipelineDetail("candidate", "candidate-one", signal);
  await actual.advanceSourcePipeline(item, "csrf");
  await actual.retrySourcePipeline(item, retry, "csrf");
  expect(adminSourcePipeline).toHaveBeenCalledWith({ limit: 20, q: "Python" }, { signal });
  expect(adminSourcePipelineColumn).toHaveBeenCalledWith(
    "profile",
    { limit: 20, source_type: "aggregator", cursor: 20 },
    { signal },
  );
  expect(adminSourcePipelineDetail).toHaveBeenCalledWith("candidate", "candidate-one", { signal });
  expect(adminSourcePipelineAdvance).toHaveBeenCalledWith(
    "source",
    item.id,
    { expected_snapshot: item.snapshot },
    { headers: { "X-CSRF-Token": "csrf" } },
  );
  expect(adminSourcePipelineRetry).toHaveBeenCalledWith(
    "source",
    item.id,
    { expected_snapshot: item.snapshot, target: "profile", job_id: retry.job_id },
    { headers: { "X-CSRF-Token": "csrf" } },
  );
});

it("preserves the source list default and its existing filters", async () => {
  navigation.query = "approval_status=pending&limit=50&q=Python";
  renderAdmin(<ResourceList resource="sources" />);
  await screen.findByRole("table");
  expect(listRecords).toHaveBeenCalledWith(
    "sources",
    expect.objectContaining({ approval_status: "pending", limit: 50, q: "Python", sort: "name" }),
    expect.any(AbortSignal),
  );
  expect(getSourcePipeline).not.toHaveBeenCalled();
  const views = screen.getByRole("navigation", { name: "Source views" });
  expect(within(views).getByRole("link", { name: "List" }).getAttribute("aria-current")).toBe(
    "page",
  );
  const href = within(views).getByRole("link", { name: "Pipeline" }).getAttribute("href")!;
  expect(href).toContain("view=pipeline");
  expect(href).not.toContain("approval_status");
  expect(screen.getByRole("link", { name: "Import" }).getAttribute("href")).toBe(
    "/content/sources/import",
  );
});

it("shows Pipeline without loading the source table and clears board-only filters for List", async () => {
  navigation.query =
    "view=pipeline&stage=profile&source_type=publisher&needs_attention=true&terminal_since=2026-10-12T00:00:00Z&q=Python";
  renderAdmin(<ResourceList resource="sources" />);
  await screen.findByRole("link", { name: item.name });
  expect(listRecords).not.toHaveBeenCalled();
  const href = within(screen.getByRole("navigation", { name: "Source views" }))
    .getByRole("link", { name: "List" })
    .getAttribute("href")!;
  expect(href).toContain("view=list");
  expect(href).toContain("q=Python");
  for (const key of ["stage", "source_type", "needs_attention", "terminal_since"])
    expect(new URL(href, "https://admin.example").searchParams.has(key)).toBe(false);
});

it.each([
  ["profile", "waiting", "Waiting", "amber"],
  ["profile", "running", "Running", "blue"],
  ["assessment", "retry_wait", "Waiting to retry", "violet"],
  ["branding", "blocked", "Blocked", "rose"],
  ["review", "ready", "Ready", "emerald"],
  ["profile", "complete", "Complete", "slate"],
  ["active", "complete", "Active", "green"],
  ["paused", "complete", "Paused", "amber"],
  ["rejected", "complete", "Rejected", "rose"],
] as const)(
  "renders a compact %s/%s card with accessible status",
  async (stage, state, label, hue) => {
    vi.mocked(getSourcePipeline).mockResolvedValue(board({ ...item, stage, state }));
    renderAdmin(<SourcePipeline query={`view=pipeline&stage=${stage}`} />);
    const link = await screen.findByRole("link", { name: item.name });
    const card = link.closest("article")!;
    expect(link.getAttribute("href")).toBe(`/content/sources/${item.id}`);
    expect(within(card).getByText("engineering.example")).toBeDefined();
    expect(within(card).getByRole("img", { name: label })).toBeDefined();
    expect(card.className).toContain(`border-${hue}`);
    expect(within(card).queryByText(label)).toBeNull();
    expect(card.querySelector("time")).toBeNull();
    expect(card.querySelectorAll("p")).toHaveLength(1);
  },
);

it("automatically moves source stages every five seconds when ordinary refresh is off", async () => {
  vi.useFakeTimers();
  vi.mocked(getSourcePipeline)
    .mockResolvedValueOnce(board())
    .mockResolvedValue(board({ ...item, stage: "branding", state: "waiting" }));
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <SourcePipeline query="view=pipeline" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  expect(
    screen
      .getByRole("link", { name: item.name })
      .closest("article")
      ?.getAttribute("data-board-stage"),
  ).toBe("profile");
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(
    screen
      .getByRole("link", { name: item.name })
      .closest("article")
      ?.getAttribute("data-board-stage"),
  ).toBe("branding");
  expect(screen.queryByRole("button", { name: /Refresh/ })).toBeNull();
});

it("persists collapsed and hidden columns independently and keeps a visible mobile stage", async () => {
  const view = renderAdmin(<SourcePipeline query="view=pipeline&stage=profile" />);
  await screen.findByRole("link", { name: item.name });
  fireEvent.doubleClick(screen.getByRole("heading", { name: "Profile", level: 2 }));
  expect(
    screen.getByRole("button", { name: "Expand Profile column" }).getAttribute("aria-expanded"),
  ).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Profile" }));
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  expect(screen.queryByRole("heading", { name: "Profile", level: 2 })).toBeNull();
  expect(screen.getByRole("combobox", { name: "Source pipeline stage" }).textContent).not.toContain(
    "Profile",
  );
  view.unmount();
  renderAdmin(<SourcePipeline query="view=pipeline&stage=profile&q=Python" />);
  await screen.findByRole("heading", { name: "Discovery", level: 2 });
  expect(screen.queryByRole("heading", { name: "Profile", level: 2 })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("button", { name: "Show all columns" }));
  expect(screen.getByRole("button", { name: "Expand Profile column" })).toBeDefined();
  expect(JSON.parse(localStorage.getItem(sourceColumnsStorageKey)!)).toEqual({
    hidden: [],
    collapsed: ["profile"],
  });
});

it("pages without duplicate cards and matches running-first FIFO order including microseconds", async () => {
  const initial = board({ ...item, id: "older", key: "source:z", state: "waiting" });
  const lane = initial.columns.find((column) => column.stage === "profile")!;
  const later = {
    ...item,
    id: "later",
    key: "source:a",
    name: "Later source",
    state: "waiting" as const,
    created_at: "2026-10-19T08:00:00.000002Z",
  };
  lane.items.push(later);
  lane.total = 3;
  lane.next_cursor = 20;
  initial.counts.profile = 3;
  vi.mocked(getSourcePipeline).mockResolvedValue(initial);
  vi.mocked(getSourcePipelineColumn).mockResolvedValue({
    ...lane,
    items: [
      { ...later, state: "running" },
      {
        ...item,
        id: "third",
        key: "source:b",
        name: "Third source",
        state: "waiting",
        created_at: "2026-10-19T08:00:00.000003Z",
      },
    ],
    next_cursor: null,
  });
  renderAdmin(<SourcePipeline query="view=pipeline&stage=profile" />);
  fireEvent.click(await screen.findByRole("button", { name: "Load more profile sources" }));
  await screen.findByRole("link", { name: "Third source" });
  const section = screen.getByRole("heading", { name: "Profile", level: 2 }).closest("section")!;
  expect(
    within(section)
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual(["Later source", item.name, "Third source"]);
  expect(getSourcePipelineColumn).toHaveBeenCalledWith(
    "profile",
    "view=pipeline&stage=profile",
    20,
    expect.any(AbortSignal),
  );
});

it("requests an active source retry with pause wording and retains its card until the server snapshot arrives", async () => {
  const user = userEvent.setup();
  const target = { stage: "profile" as const, job_id: "old-profile-job" };
  const active = {
    ...item,
    stage: "active" as const,
    state: "complete" as const,
    enabled: true,
    approval_status: "approved" as const,
    retry_targets: [target],
  };
  let finish!: (value: SourcePipelineBoard) => void;
  vi.mocked(getSourcePipeline)
    .mockResolvedValueOnce(board(active))
    .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
  renderAdmin(<SourcePipeline query="view=pipeline&stage=active" />);
  await user.click(await screen.findByRole("button", { name: `Source actions ${item.name}` }));
  await user.click(screen.getByRole("menuitem", { name: "Pause and retry profile" }));
  expect(retrySourcePipeline).toHaveBeenCalledWith(active, target, "test-csrf");
  expect(
    screen
      .getByRole("link", { name: item.name })
      .closest("article")
      ?.getAttribute("data-board-stage"),
  ).toBe("active");
  await act(async () =>
    finish(
      board({
        ...active,
        enabled: false,
        stage: "profile",
        state: "waiting",
        snapshot: "b".repeat(64),
      }),
    ),
  );
  expect(
    screen
      .getByRole("link", { name: item.name })
      .closest("article")
      ?.getAttribute("data-board-stage"),
  ).toBe("profile");
});

it("accepts only an internal drag onto a server-listed retry target, including a collapsed lane", async () => {
  const target = { stage: "branding" as const, job_id: "image-job" };
  const active = {
    ...item,
    stage: "active" as const,
    state: "complete" as const,
    enabled: true,
    approval_status: "approved" as const,
    retry_targets: [target],
  };
  vi.mocked(getSourcePipeline).mockResolvedValue(board(active));
  renderAdmin(<SourcePipeline query="view=pipeline&stage=active" />);
  const card = (await screen.findByRole("link", { name: item.name })).closest("article")!;
  const branding = screen.getByRole("heading", { name: "Branding", level: 2 }).closest("section")!;
  const activeLane = screen.getByRole("heading", { name: "Active", level: 2 }).closest("section")!;
  fireEvent.click(screen.getByRole("button", { name: "Collapse Branding column" }));
  const dataTransfer = transfer();
  dataTransfer.setData("application/x-devfeed-source-pipeline", active.key);
  fireEvent.drop(branding, { dataTransfer });
  expect(retrySourcePipeline).not.toHaveBeenCalled();
  fireEvent.dragStart(card, { dataTransfer });
  fireEvent.drop(activeLane, { dataTransfer });
  expect(retrySourcePipeline).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.drop(branding, { dataTransfer });
  });
  expect(retrySourcePipeline).toHaveBeenCalledExactlyOnceWith(active, target, "test-csrf");
});

it("opens an unlinked candidate dialog and uses the canonical source returned after handoff", async () => {
  vi.useFakeTimers();
  const candidate = {
    ...item,
    kind: "candidate" as const,
    key: "candidate:candidate-one",
    id: "candidate-one",
    source_id: null,
    candidate_id: "candidate-one",
    name: "Discovered source",
    stage: "discovery" as const,
    state: "blocked" as const,
  };
  const canonical = {
    ...item,
    stage: "paused" as const,
    enabled: false,
    approval_status: "approved" as const,
    can_advance: true,
    next_action: "resume",
    advance_label: "Resume ingestion",
  };
  vi.mocked(getSourcePipeline).mockResolvedValue(board(candidate));
  vi.mocked(getSourcePipelineDetail)
    .mockResolvedValueOnce(detail(candidate))
    .mockResolvedValue(detail(canonical));
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <SourcePipeline query="view=pipeline&stage=discovery" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: candidate.name }));
  });
  const dialog = screen.getByRole("dialog", { name: candidate.name });
  expect(getSourcePipelineDetail).toHaveBeenCalledWith(
    "candidate",
    candidate.id,
    expect.any(AbortSignal),
  );
  expect(within(dialog).queryByRole("link", { name: "Open source" })).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(within(dialog).getByRole("link", { name: "Open source" }).getAttribute("href")).toBe(
    `/content/sources/${canonical.id}`,
  );
  await act(async () => {
    fireEvent.click(within(dialog).getByRole("button", { name: "Resume ingestion" }));
  });
  expect(advanceSourcePipeline).toHaveBeenCalledWith(canonical, "fixture");
});

it("shows exact readiness blockers and run diagnostics with only valid job links", async () => {
  const value = detail();
  value.checks = [
    {
      key: "assessment",
      status: "blocked",
      error: "Assessment needs more evidence.",
      available_at: null,
      job_id: null,
    },
  ];
  value.blockers = ["assessment_missing", "branding_processing"];
  value.tasks = [
    {
      ...value.tasks[0],
      stage: "discovery",
      job_id: "discovery-job",
      status: "failed",
      error: "No feed found.",
    },
    {
      ...value.tasks[0],
      stage: "assessment",
      status: "queued",
      available_at: "2026-10-19T09:00:00Z",
      attempts: 2,
    },
  ];
  vi.mocked(getSourcePipelineDetail).mockResolvedValue(value);
  renderAdmin(<SourceReadiness id={item.id} />);
  expect(await screen.findByText("Assessment needs more evidence.")).toBeDefined();
  expect(screen.getByRole("list", { name: "Source blockers" }).textContent).toContain(
    "Assessment missing",
  );
  expect(screen.getByText("No feed found.")).toBeDefined();
  expect(screen.getByText("Retry available")).toBeDefined();
  expect(screen.getByRole("link", { name: "Open run" }).getAttribute("href")).toBe(
    "/jobs/enrichment/sources/profile-job",
  );
  expect(screen.getAllByRole("link", { name: "Open run" })).toHaveLength(1);
});

it("explains retry side effects for enabled sources but keeps ingestion recovery unpaused", () => {
  const enabled = {
    ...item,
    stage: "ingestion" as const,
    enabled: true,
    approval_status: "approved" as const,
  };
  expect(sourceRetryLabel(enabled, "profile")).toBe("Pause and retry profile");
  expect(sourceRetryLabel(enabled, "ingestion")).toBe("Retry ingestion");
  expect(sourceRetryLabel({ ...enabled, enabled: false }, "profile")).toBe("Retry profile");
});

it("uses safe hosts and only known processing job destinations", () => {
  expect(
    sourcePipelineHost({
      website_url: "javascript:alert(1)",
      feed_url: "https://feed.example/rss",
    }),
  ).toBe("feed.example");
  expect(sourcePipelineHost({ website_url: "invalid", feed_url: null })).toBe("Source candidate");
  expect(sourcePipelineJobHref("discovery", "job")).toBeNull();
  expect(sourcePipelineJobHref("branding", "job")).toBe("/jobs/enrichment/images/job");
});

it("allows manual approval when profile and branding are ready even if AI assessment failed", async () => {
  const value = detail({ ...item, stage: "assessment", state: "blocked", can_approve: true });
  value.checks = [
    {
      key: "assessment",
      status: "failed",
      job_id: "assessment-job",
      error: "Assessment failed.",
      available_at: null,
    },
  ];
  value.blockers = ["assessment_failed"];
  vi.mocked(getSourcePipelineDetail).mockResolvedValue(value);
  renderAdmin(<ResourceWorkflow identity={{ resource: "sources", id: item.id }} action="review" />);
  const button = await screen.findByRole("button", { name: "Apply decision" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByText("Assessment failed.")).toBeDefined();
  expect(screen.getByRole("link", { name: "Review source" }).getAttribute("href")).toBe(
    `/content/sources/${item.id}/review`,
  );
  fireEvent.click(button);
  await waitFor(() =>
    expect(adminSourceReview).toHaveBeenCalledWith(
      item.id,
      { decision: "approved", note: null },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("blocks approval while readiness is loading but leaves explicit rejection available", async () => {
  vi.mocked(getSourcePipelineDetail).mockImplementationOnce(() => new Promise(() => {}));
  renderAdmin(<ResourceWorkflow identity={{ resource: "sources", id: item.id }} action="review" />);
  const button = await screen.findByRole("button", { name: "Apply decision" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(adminSourceReview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Decision" }));
  fireEvent.click(screen.getByRole("option", { name: /^Rejected$/i }));
  fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Outside source scope." } });
  expect((button as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(button);
  await waitFor(() =>
    expect(adminSourceReview).toHaveBeenCalledWith(
      item.id,
      { decision: "rejected", note: "Outside source scope." },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("keeps feed fetching blocked until polling reports canonical ingestion admission", async () => {
  vi.useFakeTimers();
  const admitted = {
    ...item,
    stage: "active" as const,
    state: "complete" as const,
    approval_status: "approved" as const,
    enabled: true,
    can_ingest: true,
  };
  vi.mocked(getSourcePipelineDetail)
    .mockResolvedValueOnce(detail())
    .mockResolvedValue(detail(admitted));
  vi.mocked(adminSourceFetch).mockResolvedValue({ id: "ingestion-job" } as Awaited<
    ReturnType<typeof adminSourceFetch>
  >);
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <ResourceWorkflow identity={{ resource: "sources", id: item.id }} action="fetch" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  expect((screen.getByRole("button", { name: "Queue fetch" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect((screen.getByRole("button", { name: "Queue fetch" }) as HTMLButtonElement).disabled).toBe(
    false,
  );
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Queue fetch" }));
  });
  expect(adminSourceFetch).toHaveBeenCalledWith(item.id, {
    headers: { "X-CSRF-Token": "fixture" },
  });
});

it("discards an old source page after a polling snapshot moves its source to another lane", async () => {
  vi.useFakeTimers();
  const initial = board();
  initial.columns.find((column) => column.stage === "profile")!.next_cursor = 20;
  vi.mocked(getSourcePipeline)
    .mockResolvedValueOnce(initial)
    .mockResolvedValue(board({ ...item, stage: "assessment" }));
  let finish!: (value: Awaited<ReturnType<typeof getSourcePipelineColumn>>) => void;
  vi.mocked(getSourcePipelineColumn).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  await act(async () => {
    renderAdmin(
      <RefreshSettings>
        <SourcePipeline query="view=pipeline&stage=profile" />
      </RefreshSettings>,
    );
  });
  await saveRefresh(0);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Load more profile sources" }));
  });
  const signal = vi.mocked(getSourcePipelineColumn).mock.calls[0][3];
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(signal.aborted).toBe(true);
  await act(async () =>
    finish({
      stage: "profile",
      total: 2,
      items: [
        item,
        { ...item, key: "source:old-extra", id: "old-extra", name: "Stale extra source" },
      ],
      next_cursor: null,
    }),
  );
  expect(screen.queryByRole("link", { name: "Stale extra source" })).toBeNull();
  expect(screen.getAllByRole("link", { name: item.name })).toHaveLength(1);
  expect(
    screen
      .getByRole("link", { name: item.name })
      .closest("article")
      ?.getAttribute("data-board-stage"),
  ).toBe("assessment");
});

it("allows canonical reapproval of a rejected source when server preparation is ready", async () => {
  const rejected = {
    ...item,
    stage: "rejected" as const,
    state: "complete" as const,
    approval_status: "rejected" as const,
    can_approve: true,
  };
  const value = detail(rejected);
  value.checks = [
    { key: "profile", status: "complete" },
    { key: "branding", status: "complete" },
  ];
  value.blockers = [];
  value.tasks = [];
  vi.mocked(getSourcePipelineDetail).mockResolvedValue(value);
  vi.mocked(getRecord).mockResolvedValue({
    id: item.id,
    name: item.name,
    approval_status: "rejected",
    enabled: false,
  });
  renderAdmin(<ResourceWorkflow identity={{ resource: "sources", id: item.id }} action="review" />);
  const button = await screen.findByRole("button", { name: "Apply decision" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() =>
    expect(adminSourceReview).toHaveBeenCalledWith(
      item.id,
      { decision: "approved", note: null },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it.each(["pending", "rejected"] as const)(
  "blocks approval of an unprepared %s source while allowing explicit rejection",
  async (approval_status) => {
    const blocked = {
      ...item,
      approval_status,
      stage: approval_status === "rejected" ? ("rejected" as const) : ("profile" as const),
      state: "blocked" as const,
      can_approve: false,
    };
    const value = detail(blocked);
    value.checks = [{ key: "profile", status: "missing" }];
    value.blockers = ["profile_missing"];
    value.tasks = [];
    vi.mocked(getSourcePipelineDetail).mockResolvedValue(value);
    vi.mocked(getRecord).mockResolvedValue({
      id: item.id,
      name: item.name,
      approval_status,
      enabled: false,
    });
    renderAdmin(
      <ResourceWorkflow identity={{ resource: "sources", id: item.id }} action="review" />,
    );
    await screen.findByText(
      "Complete the source profile and branding before approving. Manual rejection remains available.",
    );
    const button = screen.getByRole("button", { name: "Apply decision" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(adminSourceReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("combobox", { name: "Decision" }));
    fireEvent.click(screen.getByRole("option", { name: /^Rejected$/i }));
    fireEvent.change(screen.getByLabelText(/Reason/), {
      target: { value: "Reviewed source rejection." },
    });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() =>
      expect(adminSourceReview).toHaveBeenCalledWith(
        item.id,
        { decision: "rejected", note: "Reviewed source rejection." },
        { headers: { "X-CSRF-Token": "test-csrf" } },
      ),
    );
  },
);

it("offers review on a rejected board card solely from can_approve without enabling processing moves", async () => {
  const user = userEvent.setup();
  const rejected = {
    ...item,
    stage: "rejected" as const,
    state: "complete" as const,
    approval_status: "rejected" as const,
    can_approve: true,
    can_advance: false,
    retry_targets: [],
  };
  vi.mocked(getSourcePipeline).mockResolvedValue(board(rejected));
  renderAdmin(<SourcePipeline query="view=pipeline&stage=rejected" />);
  const card = (await screen.findByRole("link", { name: item.name })).closest("article")!;
  expect(card.getAttribute("draggable")).toBe("false");
  await user.click(within(card).getByRole("button", { name: `Source actions ${item.name}` }));
  expect(screen.getByRole("menuitem", { name: "Review source" }).getAttribute("href")).toBe(
    `/content/sources/${item.id}/review`,
  );
  expect(screen.getAllByRole("menuitem")).toHaveLength(1);
  expect(advanceSourcePipeline).not.toHaveBeenCalled();
  expect(retrySourcePipeline).not.toHaveBeenCalled();
});
