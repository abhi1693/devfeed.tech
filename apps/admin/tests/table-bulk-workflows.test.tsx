// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { TopicProposalsTable } from "@/components/organisms/topic-proposals-table";
import { RecordTable } from "@/components/organisms/record-table";
import { renderAdmin } from "./render-admin";
import * as api from "@/lib/api/generated/admin";
import { deleteRecord } from "@/lib/resource-api";
import type { TopicProposalOut } from "@/lib/api/generated/models";

vi.mock("@/lib/api/generated/admin", async original => ({ ...await original<typeof api>(), adminTopicProposalReview: vi.fn(), adminTopicProposalAnalyze: vi.fn(), adminTopicProposalDelete: vi.fn(), adminArticleReview: vi.fn(), adminSourceReview: vi.fn() }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), deleteRecord: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);
const options = { headers: { "X-CSRF-Token": "test-csrf" } };
const proposal: TopicProposalOut = {
  id: "proposal-1", batch_id: "batch-1", topic_id: null, action: "create", origin: "import", source_name: "GitHub curated topics",
  proposed: { name: "React", slug: "react", kind: "technology", aliases: [], keywords: [] }, content_hash: "a".repeat(64), before: null,
  evidence: [], status: "pending", created_at: "2026-09-09T00:00:00Z", created_by: {}, reviewed_at: null, reviewed_by: {}, review_note: null, applied: null,
};
function proposals(items: TopicProposalOut[] = [proposal, { ...proposal, id: "proposal-2", proposed: { ...proposal.proposed, name: "Vue", slug: "vue" }, content_hash: "b".repeat(64) }]) {
  return renderAdmin(<TopicProposalsTable page={{ items, total: 1264, limit: 25, offset: 0 }} loading={false} status="pending" filtered={false} sort="slug" limit={25} offset={0} onChange={vi.fn()} onRetry={vi.fn()} onClearFilters={vi.fn()} />);
}
function selectAll() { fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" })); }
function action(name: string) { fireEvent.click(within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name })); }

it.each(["Approve", "Reject"])("bulk %s reviews only selected proposals using the displayed hash and CSRF", async label => {
  proposals();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select React" }));
  action(label);
  expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: `Confirm ${label.toLowerCase()}` }));
  await waitFor(() => expect(api.adminTopicProposalReview).toHaveBeenCalledTimes(1));
  expect(api.adminTopicProposalReview).toHaveBeenCalledWith(proposal.id, {
    decision: label === "Approve" ? "approved" : "rejected", expected_input_hash: proposal.content_hash,
    ...(label === "Approve" ? { topic: proposal.proposed } : {}),
  }, options);
  expect(api.adminTopicProposalAnalyze).not.toHaveBeenCalled();
});

it("queues selected AI research without approving or processing the other 1262 proposals", async () => {
  proposals(); selectAll(); action("AI analysis");
  fireEvent.click(screen.getByRole("button", { name: "Confirm ai analysis" }));
  await waitFor(() => expect(api.adminTopicProposalAnalyze).toHaveBeenCalledTimes(2));
  expect(api.adminTopicProposalAnalyze).toHaveBeenCalledWith(proposal.id, options);
  expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
});

it("bulk deletes reviewed proposals with their current content and review status", async () => {
  proposals([{ ...proposal, status: "approved", applied: proposal.proposed }]); selectAll();
  expect((within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: "Approve (0)" }) as HTMLButtonElement).disabled).toBe(true);
  action("Delete");
  expect(screen.getByText(/Active topics will remain/)).toBeDefined();
  fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  await waitFor(() => expect(api.adminTopicProposalDelete).toHaveBeenCalledWith(proposal.id, { expected_input_hash: proposal.content_hash, expected_status: "approved" }, options));
});

it("blocks review and delete on proposals with queued analysis", () => {
  proposals([{ ...proposal, analysis: { id: "job-1", status: "queued", attempts: 0, created_at: "2026-09-09T00:00:00Z", finished_at: null, outcome: null, error: null, model: null } }]);
  selectAll();
  for (const name of ["Approve (0)", "Reject (0)", "AI analysis (0)", "Delete (0)"]) {
    expect((within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  }
});

it.each(["topics", "tags", "topic-relations", "articles", "sources"] as const)("offers guarded bulk deletion for %s", async resource => {
  const record = { id: "record-1", name: "Example", title: "Example", publication_status: "unpublished" };
  renderAdmin(<RecordTable resource={resource} page={{ items: [record], total: 1, limit: 25, offset: 0 }} sort="name" onChange={vi.fn()} />);
  selectAll(); action("Delete");
  fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  await waitFor(() => expect(deleteRecord).toHaveBeenCalledWith(resource, record.id, "test-csrf"));
});

it("preserves the article editorial revision for bulk review", async () => {
  renderAdmin(<RecordTable resource="articles" page={{ items: [{ id: "article-1", title: "Guide", review_status: "pending", editorial_revision: 7 }], total: 1, limit: 25, offset: 0 }} sort="title" onChange={vi.fn()} />);
  selectAll(); action("Approve"); fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
  await waitFor(() => expect(api.adminArticleReview).toHaveBeenCalledWith("article-1", { action: "approve", expected_revision: 7 }, options));
});

it("uses source approval status to enable bulk review", async () => {
  renderAdmin(<RecordTable resource="sources" page={{ items: [{ id: "source-1", name: "Feed", approval_status: "pending" }], total: 1, limit: 25, offset: 0 }} sort="name" onChange={vi.fn()} />);
  selectAll(); action("Reject"); fireEvent.click(screen.getByRole("button", { name: "Confirm reject" }));
  await waitFor(() => expect(api.adminSourceReview).toHaveBeenCalledWith("source-1", { decision: "rejected" }, options));
});

it("allows job selection for retries without edit, delete or export controls", () => {
  renderAdmin(<RecordTable resource="analysis-jobs" page={{ items: [{ id: "same-id", kind: "analysis" }, { id: "same-id", kind: "topic-analysis" }], total: 2, limit: 25, offset: 0 }} sort="-created_at" onChange={vi.fn()} />);
  expect(screen.getByRole("checkbox", { name: "Select all on this page" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Export/ })).toBeNull();
});
