// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { RelationshipDiscovery } from "@/components/organisms/relationship-discovery";
import { RelationshipProposals, RelationshipProposalReview } from "@/components/organisms/relationship-proposals";
import { RecordTable } from "@/components/organisms/record-table";
import { RelatedRecords } from "@/components/organisms/related-records";
import { TopicAddMenu } from "@/components/molecules/topic-add-menu";
import { EntityPicker } from "@/components/molecules/entity-picker";
import { renderAdmin } from "./render-admin";
import * as api from "@/lib/api/generated/admin";
import * as records from "@/lib/resource-api";
import { resolveAdminRoute } from "@/lib/routes";
import type { AdminTopicOut, AdminJobOut, RelationshipProposalOut } from "@/lib/api/generated/models";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), query: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams(router.query) }));
vi.mock("@/lib/api/generated/admin", async original => ({ ...await original<typeof api>(), adminTopicGet: vi.fn(), adminTopicRelationshipsAnalyze: vi.fn(), adminRelationshipsList: vi.fn(), adminRelationshipProposalsList: vi.fn(), adminRelationshipProposalGet: vi.fn(), adminRelationshipProposalReview: vi.fn(), adminRelationshipProposalDelete: vi.fn() }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof records>(), listRecords: vi.fn(), getRecord: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
const csrf = { headers: { "X-CSRF-Token": "test-csrf" } };
const topic = { id: "react", name: "React", slug: "react", kind: "technology", status: "active", aliases: [], keywords: [], facts: [] } as unknown as AdminTopicOut;
const proposal: RelationshipProposalOut = { id: "proposal", job_id: "job", topic_id: "react", related_topic_id: "javascript", topic_name: "React", related_topic_name: "JavaScript", relation: "uses_language", explanation: "React uses JavaScript.", evidence_url: "https://react.dev", evidence_title: "React documentation", evidence_quote: "The library for web and native user interfaces", status: "pending", created_at: "2026-09-09T00:00:00Z", created_by: { name: "Admin" }, reviewed_at: null, reviewed_by: null, review_note: null, content_hash: "a".repeat(64), can_approve: true, approval_blocker: null };
beforeEach(() => {
  vi.resetAllMocks(); router.query = "";
  vi.mocked(api.adminTopicGet).mockResolvedValue(topic);
  vi.mocked(records.getRecord).mockResolvedValue({ ...topic });
  vi.mocked(records.listRecords).mockResolvedValue({ items: [{ ...topic }], total: 1, offset: 0, limit: 25 });
  vi.mocked(api.adminTopicRelationshipsAnalyze).mockResolvedValue({ id: "job", kind: "topic-analysis", topic_id: "react", status: "queued" } as AdminJobOut);
  vi.mocked(api.adminRelationshipProposalsList).mockResolvedValue({ items: [proposal], total: 1, offset: 0, limit: 25 });
  vi.mocked(api.adminRelationshipProposalGet).mockResolvedValue(proposal);
  vi.mocked(api.adminRelationshipProposalReview).mockResolvedValue({ ...proposal, status: "approved", can_approve: false });
});
afterEach(cleanup);

const pendingRow = () => ({ id: `proposal~${proposal.id}`, topic_id: proposal.topic_id, related_topic_id: proposal.related_topic_id, topic_name: proposal.topic_name, related_topic_name: proposal.related_topic_name, relation: proposal.relation, status: "pending" as const, evidence_url: proposal.evidence_url, proposal });
const approvedRow = () => ({ ...pendingRow(), id: "react~typescript~related_to", related_topic_id: "typescript", related_topic_name: "TypeScript", relation: "related_to" as const, status: "approved" as const, proposal: null });

it("loads combined relationships through the typed adapter with distinct row identities", async () => {
  const { listRecords } = await vi.importActual<typeof records>("@/lib/resource-api");
  vi.mocked(api.adminRelationshipsList).mockResolvedValue({ items: [pendingRow(), approvedRow()], total: 2, offset: 0, limit: 10 });
  const signal = new AbortController().signal;
  const page = await listRecords("topic-relations", { topic_id: "react", limit: 10, offset: 0, sort: "relation" }, signal);
  expect(api.adminRelationshipsList).toHaveBeenCalledWith({ topic_id: "react", limit: 10, offset: 0, sort: "relation" }, { signal });
  expect(page.items.map(row => row.id)).toEqual(["proposal~proposal", "react~typescript~related_to"]);
});

it.each(["approved", "rejected"] as const)("reviews pending relationships as %s directly in the topic table", async decision => {
  vi.mocked(records.listRecords).mockResolvedValue({ items: [pendingRow(), approvedRow()], total: 2, offset: 0, limit: 10 });
  renderAdmin(<RelatedRecords resource="topic-relations" filter={{ topic_id: "react" }} />);
  await screen.findByRole("heading", { name: "Topic relationships (2)" });
  expect(screen.getByText(proposal.explanation)).toBeDefined();
  expect(screen.getByText(proposal.evidence_quote)).toBeDefined();
  expect(screen.getByRole("link", { name: proposal.evidence_title }).getAttribute("href")).toBe(proposal.evidence_url);
  expect(screen.getByText("Pending")).toBeDefined();
  expect(screen.getByText("Approved")).toBeDefined();
  expect(screen.getByRole("columnheader", { name: "Related topic" })).toBeDefined();
  expect(screen.queryByRole("columnheader", { name: "From topic" })).toBeNull();
  expect(screen.queryByRole("columnheader", { name: "To topic" })).toBeNull();
  expect(screen.queryByRole("link", { name: "React" })).toBeNull();
  expect(screen.queryByRole("link", { name: /Review/ })).toBeNull();
  const reviewed = { ...pendingRow(), id: "react~javascript~uses_language", status: "approved", proposal: null };
  vi.mocked(records.listRecords).mockResolvedValue({ items: decision === "approved" ? [reviewed, approvedRow()] : [approvedRow()], total: decision === "approved" ? 2 : 1, offset: 0, limit: 10 });
  fireEvent.click(screen.getByRole("button", { name: `${decision === "approved" ? "Approve" : "Reject"} React → JavaScript (Uses language)` }));
  await waitFor(() => expect(api.adminRelationshipProposalReview).toHaveBeenCalledWith("proposal", { decision, expected_input_hash: proposal.content_hash }, csrf));
  await waitFor(() => expect(screen.queryByText("Pending")).toBeNull());
  expect(router.push).not.toHaveBeenCalled();
});

it.each([
  ["uses_language", "Used by"], ["depends_on", "Required by"], ["implements", "Implemented by"], ["part_of", "Contains"], ["related_to", "Related to"],
] as const)("shows incoming %s from the current topic's perspective", (relation, expected) => {
  const row = { ...approvedRow(), topic_id: "typescript", topic_name: "TypeScript", related_topic_id: "react", related_topic_name: "React", relation };
  renderAdmin(<RecordTable resource="topic-relations" topicId="react" page={{ items: [row], total: 1, offset: 0, limit: 10 }} sort="relation" onChange={vi.fn()} />);
  expect(screen.getByText(expected)).toBeDefined();
  expect(screen.getByRole("link", { name: "TypeScript" }).getAttribute("href")).toBe("/taxonomy/topics/typescript");
  expect(screen.queryByRole("link", { name: "React" })).toBeNull();
});

it("retains both endpoints in the unfiltered relationships table", () => {
  renderAdmin(<RecordTable resource="topic-relations" page={{ items: [approvedRow()], total: 1, offset: 0, limit: 10 }} sort="relation" onChange={vi.fn()} />);
  expect(screen.getByRole("columnheader", { name: "From topic" })).toBeDefined();
  expect(screen.getByRole("columnheader", { name: "To topic" })).toBeDefined();
});

it("keeps stale proposals visible with rejection available and approval disabled", async () => {
  const row = { ...pendingRow(), proposal: { ...proposal, can_approve: false, approval_blocker: "A topic changed after research" } };
  renderAdmin(<RecordTable resource="topic-relations" page={{ items: [row], total: 1, offset: 0, limit: 10 }} sort="relation" onChange={vi.fn()} />);
  expect(screen.getByText("A topic changed after research")).toBeDefined();
  expect((screen.getByRole("button", { name: "Approve React → JavaScript (Uses language)" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Reject React → JavaScript (Uses language)" }) as HTMLButtonElement).disabled).toBe(false);
});

it("bulk approval in the combined table only applies pending eligible suggestions", async () => {
  renderAdmin(<RecordTable resource="topic-relations" page={{ items: [pendingRow(), approvedRow()], total: 2, offset: 0, limit: 10 }} sort="relation" onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: /Approve/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
  await waitFor(() => expect(api.adminRelationshipProposalReview).toHaveBeenCalledTimes(1));
  expect(api.adminRelationshipProposalReview).toHaveBeenCalledWith("proposal", { decision: "approved", expected_input_hash: proposal.content_hash }, csrf);
});

it("uses natural discovery and review routes before interpreting relationship IDs", () => {
  expect(resolveAdminRoute(["taxonomy", "relationships", "discover"])).toEqual({ view: "relationship-discover" });
  expect(resolveAdminRoute(["taxonomy", "relationships", "proposals"])).toEqual({ view: "relationship-proposals" });
  expect(resolveAdminRoute(["taxonomy", "relationships", "proposals", "proposal"])).toEqual({ view: "relationship-proposal", id: "proposal" });
  expect(resolveAdminRoute(["taxonomy", "relationships", "react", "uses_language", "javascript"])).toEqual({ view: "detail", resource: "topic-relations", id: "react~javascript~uses_language", section: "details" });
});

it("queues research from an active topic and opens the existing analysis run", async () => {
  router.query = "topic_id=react";
  renderAdmin(<RelationshipDiscovery />);
  await waitFor(() => expect((screen.getByRole("button", { name: "Discover with AI" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Discover with AI" }));
  await waitFor(() => expect(api.adminTopicRelationshipsAnalyze).toHaveBeenCalledWith("react", { related_topic_id: null }, csrf));
  expect(router.push).toHaveBeenCalledWith("/jobs/analysis/topics/job");
  expect(api.adminRelationshipProposalReview).not.toHaveBeenCalled();
});

it("does not start research for an inactive topic from a bookmarked URL", async () => {
  router.query = "topic_id=react";
  vi.mocked(api.adminTopicGet).mockResolvedValue({ ...topic, status: "proposed" });
  renderAdmin(<RelationshipDiscovery />);
  await screen.findByText("Select an active topic to research.");
  expect((screen.getByRole("button", { name: "Discover with AI" }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.adminTopicRelationshipsAnalyze).not.toHaveBeenCalled();
});

it("requests only active topics through the shared entity picker", async () => {
  renderAdmin(<EntityPicker resource="topics" label="Topic" status="active" value="" onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Topic" }));
  await waitFor(() => expect(records.listRecords).toHaveBeenCalledWith("topics", expect.objectContaining({ status: "active", limit: 25 }), expect.any(AbortSignal)));
});

it("bulk research skips proposed and rejected topics", async () => {
  renderAdmin(<RecordTable resource="topics" page={{ items: [{ ...topic }, { ...topic, id: "pending", status: "proposed" }, { ...topic, id: "rejected", status: "rejected" }], total: 3, offset: 0, limit: 25 }} sort="name" onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: /Discover relationships/ }));
  expect(api.adminTopicRelationshipsAnalyze).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm discover relationships" }));
  await waitFor(() => expect(api.adminTopicRelationshipsAnalyze).toHaveBeenCalledTimes(1));
  expect(api.adminTopicRelationshipsAnalyze).toHaveBeenCalledWith("react", {}, csrf);
});

it("loads all matching relationship proposals and approves only eligible snapshots", async () => {
  router.query = "q=react&topic_id=react&job_id=job";
  const stale = { ...proposal, id: "stale", related_topic_name: "Changed", can_approve: false, approval_blocker: "Both topics must still be active before approval" };
  vi.mocked(api.adminRelationshipProposalsList).mockResolvedValueOnce({ items: [proposal], total: 2, offset: 0, limit: 25 })
    .mockResolvedValueOnce({ items: [proposal, stale], total: 2, offset: 0, limit: 100 });
  renderAdmin(<RelationshipProposals />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select React → JavaScript (Uses language)" }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 2 matching records" }));
  await screen.findByText("2 selected across all pages");
  expect(api.adminRelationshipProposalsList).toHaveBeenCalledWith(expect.objectContaining({ q: "react", topic_id: "react", job_id: "job", status: "pending", limit: 100 }), { signal: expect.any(AbortSignal) });
  fireEvent.click(within(screen.getByRole("region", { name: "Selected rows" })).getByRole("button", { name: /Approve/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
  await waitFor(() => expect(api.adminRelationshipProposalReview).toHaveBeenCalledTimes(1));
  expect(api.adminRelationshipProposalReview).toHaveBeenCalledWith("proposal", { decision: "approved", expected_input_hash: proposal.content_hash }, csrf);
});

it("shows evidence and preserves the displayed hash when reviewing", async () => {
  renderAdmin(<RelationshipProposalReview id="proposal" />);
  expect((await screen.findByRole("link", { name: "React documentation" })).getAttribute("href")).toBe("https://react.dev");
  fireEvent.change(screen.getByRole("textbox", { name: "Review note" }), { target: { value: "Verified the official documentation" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve relationship" }));
  await waitFor(() => expect(api.adminRelationshipProposalReview).toHaveBeenCalledWith("proposal", { decision: "approved", expected_input_hash: proposal.content_hash, note: "Verified the official documentation" }, csrf));
  expect((await screen.findByRole("link", { name: "View relationships" })).getAttribute("href")).toBe("/taxonomy/relationships?topic_id=react");
});

it("allows rejection when changed or inactive topics block approval", async () => {
  vi.mocked(api.adminRelationshipProposalGet).mockResolvedValue({ ...proposal, can_approve: false, approval_blocker: "Both topics must still be active before approval" });
  renderAdmin(<RelationshipProposalReview id="proposal" />);
  await screen.findByText("Both topics must still be active before approval");
  expect((screen.getByRole("button", { name: "Approve relationship" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(api.adminRelationshipProposalReview).toHaveBeenCalledWith("proposal", { decision: "rejected", expected_input_hash: proposal.content_hash, note: null }, csrf));
});

it("links relationship runs to their active topic in the common jobs table", () => {
  renderAdmin(<RecordTable resource="analysis-jobs" page={{ items: [{ id: "job", kind: "topic-analysis", topic_id: "react", target_name: "React", status: "running" }], total: 1, offset: 0, limit: 25 }} sort="-created_at" onChange={vi.fn()} />);
  expect(screen.getByText("Relationships")).toBeDefined();
  expect(screen.getByRole("link", { name: "React" }).getAttribute("href")).toBe("/taxonomy/topics/react");
});

it("keeps manual creation and AI discovery in the shared add menu", async () => {
  const { default: userEvent } = await import("@testing-library/user-event");
  renderAdmin(<TopicAddMenu relationships />);
  await userEvent.click(screen.getByRole("button", { name: "Add relationship" }));
  for (const [name, href] of [["Create relationship", "/taxonomy/relationships/new"], ["Discover with AI", "/taxonomy/relationships/discover"], ["Review proposals", "/taxonomy/relationships/proposals"]]) expect(screen.getByRole("menuitem", { name }).getAttribute("href")).toBe(href);
});
