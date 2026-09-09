// @vitest-environment jsdom
import { renderAdmin } from "./render-admin";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { getRecord, listRecords } from "@/lib/resource-api";
import { adminAiAnalysisJobsList, adminJobGet, adminJobLogs, adminJobsList } from "@/lib/api/generated/admin";
import type { AdminJobOut } from "@/lib/api/generated/models";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), query: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams(router.query) }));
vi.mock("@/lib/api/generated/admin", () => ({ adminAiAnalysisJobsList: vi.fn(), adminJobGet: vi.fn(), adminJobLogs: vi.fn(), adminJobsList: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
const topic: AdminJobOut = { id: "topic-job", kind: "topic-analysis", target_name: "React", proposal_id: "proposal-1", status: "queued", attempts: 0, created_at: "2026-09-08T00:00:00Z", available_at: "2026-09-08T00:00:00Z", finished_at: null, error: null, details: {} };
const article: AdminJobOut = { ...topic, id: "article-job", kind: "analysis", target_name: "React guide", article_id: "article-1", proposal_id: null };
const page = { items: [topic, article], total: 2, offset: 0, limit: 25 };

beforeEach(() => {
  vi.clearAllMocks(); router.query = "";
  vi.mocked(adminAiAnalysisJobsList).mockResolvedValue(page);
  vi.mocked(adminJobGet).mockResolvedValue(topic);
  vi.mocked(adminJobLogs).mockResolvedValue({ items: [], next_cursor: null, has_more: false, truncated: false, unreadable_entries: 0, retention_seconds: 604800, max_entries: 1000, job_status: "queued", attempts: 0 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("lists both pipelines with subject links and a type filter", async () => {
  renderAdmin(<ResourceList resource="analysis-jobs" />);
  const table = await screen.findByRole("table", { name: "AI analysis" });
  expect(adminAiAnalysisJobsList).toHaveBeenCalled(); expect(adminJobsList).not.toHaveBeenCalled();
  expect(within(table).getByText("Topic")).toBeDefined(); expect(within(table).getByText("Article")).toBeDefined();
  expect(within(table).getByRole("link", { name: "React" }).getAttribute("href")).toBe("/taxonomy/topics/proposals/proposal-1");
  expect(within(table).getByRole("link", { name: "React guide" }).getAttribute("href")).toBe("/content/articles/article-1");
  expect(within(table).getByRole("link", { name: "topic-jo" }).getAttribute("href")).toBe("/jobs/analysis/topics/topic-job");
  expect(within(table).getByRole("link", { name: "article-" }).getAttribute("href")).toBe("/jobs/analysis/articles/article-job");
  fireEvent.click(screen.getByRole("combobox", { name: "Analysis type" }));
  fireEvent.click(screen.getByRole("option", { name: "Topics" }));
  expect(router.push).toHaveBeenCalledWith("/jobs/analysis/topics?limit=25&offset=0", { scroll: false });
});

it("opens the correct topic run and retains its pipeline in logs navigation", async () => {
  render(<ResourceDetail resource="analysis-jobs" id="topic-analysis~topic-job" />);
  await screen.findByRole("heading", { name: "Run topic-jo" });
  expect(adminJobGet).toHaveBeenCalledWith("topic-analysis", "topic-job", { signal: expect.any(AbortSignal) });
  await waitFor(() => expect(adminJobLogs).toHaveBeenCalledWith("topic-analysis", "topic-job", expect.anything(), expect.anything()));
  expect(screen.getByRole("link", { name: "Logs" }).getAttribute("href")).toBe("/jobs/analysis/topics/topic-job/logs");
  expect(screen.getByRole("link", { name: "Review topic proposal" }).getAttribute("href")).toBe("/taxonomy/topics/proposals/proposal-1");
});

it("keeps article run URLs and related-article filtering compatible", async () => {
  await getRecord("analysis-jobs", "article-job");
  expect(adminJobGet).toHaveBeenCalledWith("analysis", "article-job", { signal: undefined });
  await listRecords("analysis-jobs", { article_id: "article-1", offset: 25, limit: 25 });
  expect(adminAiAnalysisJobsList).toHaveBeenCalledWith({ article_id: "article-1", offset: 25, limit: 25 }, { signal: undefined });
});

it("refreshes runs without clearing the table or search input and stops after unmount", async () => {
  vi.useFakeTimers();
  let view!: ReturnType<typeof render>;
  await act(async () => { view = renderAdmin(<ResourceList resource="analysis-jobs" />); });
  fireEvent.change(screen.getByRole("textbox", { name: "Search ai analysis" }), { target: { value: "draft search" } });
  let resolve!: (value: typeof page) => void;
  vi.mocked(adminAiAnalysisJobsList).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(screen.getByRole("table", { name: "AI analysis" })).toBeDefined();
  expect((screen.getByRole("textbox", { name: "Search ai analysis" }) as HTMLInputElement).value).toBe("draft search");
  await act(async () => { resolve({ ...page, items: [{ ...topic, status: "running" }] }); });
  expect(screen.getByText("Running")).toBeDefined();
  view.unmount(); const count = vi.mocked(adminAiAnalysisJobsList).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(adminAiAnalysisJobsList).toHaveBeenCalledTimes(count);
});


it("derives analysis type from the path and keeps it during search and pagination", async () => {
  router.query = "status=running&offset=25&limit=25";
  vi.mocked(adminAiAnalysisJobsList).mockResolvedValue({ ...page, total: 80, offset: 25 });
  renderAdmin(<ResourceList resource="analysis-jobs" analysisType="topics" />);
  await screen.findByRole("table", { name: "AI analysis" });
  expect(adminAiAnalysisJobsList).toHaveBeenCalledWith(expect.objectContaining({ analysis_type: "topics", status: "running", offset: 25 }), expect.anything());
  await waitFor(() => expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(router.push).toHaveBeenLastCalledWith("/jobs/analysis/topics?status=running&offset=50&limit=25", { scroll: false });
  fireEvent.change(screen.getByRole("textbox", { name: "Search ai analysis" }), { target: { value: "React" } });
  await waitFor(() => expect(router.replace).toHaveBeenLastCalledWith("/jobs/analysis/topics?status=running&offset=0&limit=25&q=React", { scroll: false }));
  fireEvent.click(screen.getByRole("combobox", { name: "Analysis type" }));
  fireEvent.click(screen.getByRole("option", { name: "Articles" }));
  expect(router.push).toHaveBeenLastCalledWith("/jobs/analysis/articles?status=running&offset=0&limit=25", { scroll: false });
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(router.push).toHaveBeenLastCalledWith("/jobs/analysis?offset=0");
});
