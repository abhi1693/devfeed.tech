// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ResourceList } from "@/components/organisms/resource-list";
import { RelatedRecords } from "@/components/organisms/related-records";
import { RecordTable } from "@/components/organisms/record-table";
import { listRecords, getRecord, jobKinds, type RecordData } from "@/lib/resource-api";
import { adminJobRetry } from "@/lib/api/generated/admin";
import type { AdminJobOut } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { type Resource } from "@/lib/resources";
import { renderAdmin } from "./render-admin";
import { toast } from "sonner";

const router = vi.hoisted(() => ({ push: vi.fn(), query: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams(router.query) }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), listRecords: vi.fn(), getRecord: vi.fn() }));
vi.mock("@/lib/api/generated/admin", async original => ({ ...await original<typeof import("@/lib/api/generated/admin")>(), adminJobRetry: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
const job = (id: string, kind: AdminJobOut["kind"] = "analysis", status = "failed"): RecordData & AdminJobOut => ({ id, kind, status, retryable: status === "failed", attempts: 3, created_at: "2026-09-09T00:00:00Z", available_at: "2026-09-09T00:00:00Z", finished_at: null, error: null, details: {}, target_name: `Subject ${id}` });
const page = (items: RecordData[], total = items.length, offset = 0) => ({ items, total, offset, limit: 25 });
beforeEach(() => {
  vi.clearAllMocks(); router.query = "";
  vi.mocked(listRecords).mockResolvedValue(page([job("failed")]));
  vi.mocked(adminJobRetry).mockResolvedValue({ ...job("new"), status: "queued" } as Awaited<ReturnType<typeof adminJobRetry>>);
});
afterEach(cleanup);

it.each(Object.keys(jobKinds) as Resource[])("offers retry all failed in %s", async resource => {
  vi.mocked(listRecords).mockResolvedValue(page([job("failed", jobKinds[resource])]));
  renderAdmin(<ResourceList resource={resource} />);
  const button = await screen.findByRole("button", { name: "Retry all failed" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  const confirm = await screen.findByRole("button", { name: "Confirm retry" });
  expect(adminJobRetry).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(adminJobRetry).toHaveBeenCalledWith(jobKinds[resource], "failed", { headers: { "X-CSRF-Token": "test-csrf" } }));
});

it("loads all failed pages before mutation, preserving type, search and subject filters", async () => {
  router.query = "q=React&status=running&offset=25&limit=25&article_id=article-1";
  const failed = Array.from({ length: 103 }, (_, index) => job(String(index), "topic-analysis"));
  vi.mocked(listRecords).mockImplementation(async (_, params = {}) => params.status === "failed" ? page(failed.slice(Number(params.offset), Number(params.offset) + Number(params.limit)), 103, Number(params.offset)) : page([job("running", "topic-analysis", "running")]));
  renderAdmin(<ResourceList resource="analysis-jobs" analysisType="topics" />);
  await screen.findByText("Running");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  await screen.findByRole("dialog", { name: "Retry 103 selected records?" });
  expect(adminJobRetry).not.toHaveBeenCalled();
  for (const offset of [0, 100]) expect(listRecords).toHaveBeenCalledWith("analysis-jobs", expect.objectContaining({ q: "React", status: "failed", retryable_only: "true", offset, limit: 100, article_id: "article-1", analysis_type: "topics" }), expect.any(AbortSignal));
  fireEvent.click(screen.getByRole("button", { name: "Confirm retry" }));
  await waitFor(() => expect(adminJobRetry).toHaveBeenCalledTimes(103));
  expect(vi.mocked(adminJobRetry).mock.calls.every(([kind]) => kind === "topic-analysis")).toBe(true);
});

it("keeps both AI pipelines when their job IDs collide", async () => {
  vi.mocked(listRecords).mockResolvedValue(page([job("shared", "analysis"), job("shared", "topic-analysis")]));
  renderAdmin(<ResourceList resource="analysis-jobs" />);
  await screen.findByText("Article");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm retry" }));
  await waitFor(() => expect(adminJobRetry).toHaveBeenCalledTimes(2));
  expect(vi.mocked(adminJobRetry).mock.calls.map(([kind]) => kind).sort()).toEqual(["analysis", "topic-analysis"]);
});

it("retries only failed selected rows and retains individual error messages", async () => {
  vi.mocked(adminJobRetry).mockRejectedValueOnce(new ApiError(409, "The source is disabled"));
  renderAdmin(<RecordTable resource="ingestion-jobs" page={page([job("failed", "ingestion"), job("running", "ingestion", "running")])} sort="-created_at" onChange={vi.fn()} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: "Retry (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm retry" }));
  await screen.findByText("The source is disabled");
  expect(adminJobRetry).toHaveBeenCalledTimes(1);
  expect(adminJobRetry).toHaveBeenCalledWith("ingestion", "failed", expect.anything());
});

it("keeps related operations scoped to their subject without retry all failed", async () => {
  renderAdmin(<RelatedRecords resource="analysis-jobs" filter={{ topic_id: "react" }} />);
  await screen.findByText("Failed");
  expect(screen.queryByRole("button", { name: "Retry all failed" })).toBeNull();
  expect(listRecords).toHaveBeenCalledWith("analysis-jobs", expect.objectContaining({ topic_id: "react", offset: 0, limit: 25 }), expect.any(AbortSignal));
  expect(screen.getByRole("link", { name: "View full list" }).getAttribute("href")).toBe("/jobs/analysis?topic_id=react");
});

it("does not retry a partial selection if a later page fails", async () => {
  vi.mocked(listRecords).mockImplementation(async (_, params) => {
    if (params?.status !== "failed") return page([job("failed")]);
    if (params.offset) throw new ApiError(503, "Unavailable");
    return page([job("failed")], 2);
  });
  renderAdmin(<ResourceList resource="analysis-jobs" />);
  await screen.findByText("Failed");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not load matching records", expect.anything()));
  expect(adminJobRetry).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("reports no eligible jobs and does not mutate anything", async () => {
  vi.mocked(listRecords).mockResolvedValue(page([]));
  renderAdmin(<ResourceList resource="analysis-jobs" />);
  await screen.findByText("No analysis runs match these filters.");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  await waitFor(() => expect(toast.info).toHaveBeenCalledWith("No eligible records match these filters"));
  expect(adminJobRetry).not.toHaveBeenCalled();
});

it("ignores duplicate loads and cancels preparation when leaving the table", async () => {
  let resolve!: (value: ReturnType<typeof page>) => void;
  const pending = new Promise<ReturnType<typeof page>>(done => { resolve = done; });
  vi.mocked(listRecords).mockImplementation(async (_, params) => params?.status === "failed" ? pending : page([job("failed")]));
  const view = renderAdmin(<ResourceList resource="analysis-jobs" />);
  await screen.findByText("Failed");
  const button = screen.getByRole("button", { name: "Retry all failed" });
  fireEvent.click(button); fireEvent.click(button);
  const loads = vi.mocked(listRecords).mock.calls.filter(([, params]) => params?.status === "failed");
  expect(loads).toHaveLength(1);
  view.unmount();
  expect(loads[0][2]?.aborted).toBe(true);
  await act(async () => { resolve(page([job("failed")])); await pending; });
  expect(adminJobRetry).not.toHaveBeenCalled();
  expect(toast.error).not.toHaveBeenCalled();
});

it("does not select the old failure when retry all is used again after queuing", async () => {
  let retried = false;
  vi.mocked(listRecords).mockImplementation(async (_, params) => {
    if (retried && params?.retryable_only === "true") return page([]);
    return page([job("original", "analysis", retried ? "retried" : "failed")]);
  });
  vi.mocked(adminJobRetry).mockImplementation(async () => {
    retried = true;
    return job("new", "analysis", "queued") as Awaited<ReturnType<typeof adminJobRetry>>;
  });
  renderAdmin(<ResourceList resource="analysis-jobs" />);
  await screen.findByText("Failed");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm retry" }));
  await screen.findByText("Retried");
  fireEvent.click(screen.getByRole("button", { name: "Retry all failed" }));
  await waitFor(() => expect(toast.info).toHaveBeenCalledWith("No eligible records match these filters"));
  expect(adminJobRetry).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  const retry = within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: "Retry (0)" });
  expect((retry as HTMLButtonElement).disabled).toBe(true);
});


it("renders source names from the job page without per-row source requests", async () => {
  vi.mocked(listRecords).mockResolvedValue(page(Array.from({ length: 25 }, (_, i) => ({ ...job(String(i), "ingestion", "queued"), source_id: `source-${i}`, target_name: `Publisher ${i}` }))));
  renderAdmin(<ResourceList resource="ingestion-jobs" />);
  expect(await screen.findByRole("link", { name: "Publisher 24" })).toBeTruthy();
  expect(listRecords).toHaveBeenCalledTimes(1);
  expect(getRecord).not.toHaveBeenCalled();
});
