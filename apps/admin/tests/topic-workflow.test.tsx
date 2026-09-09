// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AdminSession } from "@/components/molecules/admin-session";
import { TopicImport } from "@/components/organisms/topic-import";
import { TopicProposalReview, TopicProposals } from "@/components/organisms/topic-proposals";
import { TopicEnrichment } from "@/components/organisms/topic-enrichment";
import { notifyFailure } from "@/lib/notifications";
import * as api from "@/lib/api/generated/admin";
import type { TopicProposalOut } from "@/lib/api/generated/models";

const router = vi.hoisted(() => ({ push: vi.fn(), query: "" }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams(router.query) }));
vi.mock("@/lib/api/generated/admin", () => ({ adminTopicProposalFilterOptions: vi.fn(), adminTopicProposalAnalyze: vi.fn(), adminTopicImportPreview: vi.fn(), adminTopicImportSubmit: vi.fn(), adminTopicProposalGet: vi.fn(), adminTopicProposalReview: vi.fn(), adminTopicEnrichmentPreview: vi.fn(), adminTopicEnrichmentSubmit: vi.fn(), adminTopicProposalsList: vi.fn(), adminTopicDiscover: vi.fn(), adminTopicGithubPull: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
const draft = { name: "Backend", slug: "backend", description: "Server engineering", keywords: ["api"], kind: "discipline", aliases: [] };
const proposal: TopicProposalOut = { id: "proposal-1", batch_id: "batch-1", topic_id: null, action: "create", origin: "import", source_name: "topics.json", proposed: draft, before: null, evidence: [{ row: 1 }], status: "pending", created_at: "2026-09-07T00:00:00Z", created_by: { subject: "importer" }, reviewed_at: null, reviewed_by: {}, review_note: null, applied: null };
function mount(child: React.ReactNode) { return render(<AdminSession admin={{ subject: "reviewer", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }}>{child}</AdminSession>); }
beforeEach(() => {
  vi.clearAllMocks();
  router.query = "";
  vi.mocked(api.adminTopicProposalFilterOptions).mockResolvedValue({ kinds: ["discipline", "language", "technology"], sources: ["GitHub curated topics", "topics.json"] });
  vi.mocked(api.adminTopicDiscover).mockResolvedValue([]);
  vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 25 });
  vi.mocked(api.adminTopicGithubPull).mockResolvedValue({ revision: "a".repeat(40), total: 2, processed: 2, created: 2, skipped: 0, issues: [], next_offset: null });
  vi.mocked(api.adminTopicImportPreview).mockResolvedValue({ rows: [{ row: 1, action: "create", topic: draft, issues: [] }], preview_token: "preview-token", can_submit: true });
  vi.mocked(api.adminTopicImportSubmit).mockResolvedValue([proposal]);
  vi.mocked(api.adminTopicProposalGet).mockResolvedValue(proposal);
  vi.mocked(api.adminTopicProposalReview).mockResolvedValue({ ...proposal, status: "approved", applied: draft, topic_id: "topic-1", reviewed_by: { subject: "reviewer" }, reviewed_at: "2026-09-07T01:00:00Z" });
  vi.mocked(api.adminTopicEnrichmentPreview).mockResolvedValue({ topic: draft, preview_token: "evidence-token", articles_examined: 2, suggestions: [{ keyword: "postgres", article_count: 2, articles: [{ id: "article-1", title: "Database design", url: "https://example.com/article" }] }] });
  vi.mocked(api.adminTopicEnrichmentSubmit).mockResolvedValue({ ...proposal, action: "update", origin: "article_enrichment" });
});
afterEach(cleanup);

it("selects all matching proposals using the same filters across every page", async () => {
  router.query = "kind=technology&source=GitHub+curated+topics&q=web&analysis=not_run&missing=description&sort=slug";
  const second = { ...proposal, id: "proposal-2", proposed: { ...proposal.proposed, name: "Frontend" } };
  vi.mocked(api.adminTopicProposalsList).mockResolvedValueOnce({ items: [proposal], total: 2, offset: 0, limit: 25 })
    .mockResolvedValueOnce({ items: [proposal], total: 2, offset: 0, limit: 100 })
    .mockResolvedValueOnce({ items: [second], total: 2, offset: 1, limit: 100 });
  mount(<TopicProposals />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select Backend" }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 2 matching records" }));
  await screen.findByText("2 selected across all pages");
  for (const offset of [0, 1]) expect(api.adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ status: "pending", kind: "technology", source: "GitHub curated topics", q: "web", analysis: "not_run", missing: "description", sort: "slug", limit: 100, offset }), { signal: expect.any(AbortSignal) });
  expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
});

describe("supervised topic workflow", () => {
  it("previews imports without creating proposals, then explicitly submits for review", async () => {
    mount(<TopicImport />);
    fireEvent.change(screen.getByLabelText("Topic data"), { target: { value: JSON.stringify([draft]) } });
    fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
    const submit = await screen.findByRole("button", { name: "Send proposals for review" });
    expect(api.adminTopicImportSubmit).not.toHaveBeenCalled();
    fireEvent.click(submit);
    await waitFor(() => expect(api.adminTopicImportSubmit).toHaveBeenCalledWith(expect.objectContaining({ preview_token: "preview-token" }), { headers: { "X-CSRF-Token": "test-csrf" } }));
    expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
    expect(router.push).toHaveBeenCalledWith("/taxonomy/topics/proposals?batch_id=batch-1");
  });
  it("invalidates a preview when its input changes", async () => {
    mount(<TopicImport />);
    fireEvent.change(screen.getByLabelText("Topic data"), { target: { value: JSON.stringify([draft]) } });
    fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
    await screen.findByRole("button", { name: "Send proposals for review" });
    fireEvent.change(screen.getByLabelText("Source name"), { target: { value: "edited.csv" } });
    expect(screen.queryByRole("button", { name: "Send proposals for review" })).toBeNull();
  });
  it("blocks submission of an invalid import and displays its row issue", async () => {
    vi.mocked(api.adminTopicImportPreview).mockResolvedValue({ rows: [{ row: 1, action: "create", topic: draft, issues: ["Duplicate slug in this import"] }], preview_token: "invalid-token", can_submit: false });
    mount(<TopicImport />);
    fireEvent.change(screen.getByLabelText("Topic data"), { target: { value: JSON.stringify([draft]) } });
    fireEvent.click(screen.getByRole("button", { name: "Preview import" }));
    const submit = await screen.findByRole("button", { name: "Send proposals for review" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Duplicate slug in this import")).toBeDefined();
    expect(api.adminTopicImportSubmit).not.toHaveBeenCalled();
  });
  it("shows the evidence and submits the administrator's edited fields only on approval", async () => {
    mount(<TopicProposalReview id="proposal-1" />);
    const name = await screen.findByLabelText("Name");
    expect(screen.getByText("Imported row 1 from topics.json")).toBeDefined();
    fireEvent.change(name, { target: { value: "Backend engineering" } });
    fireEvent.change(screen.getByLabelText("Review note"), { target: { value: "Checked scope" } });
    expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve and create topic" }));
    await waitFor(() => expect(api.adminTopicProposalReview).toHaveBeenCalledWith("proposal-1", { decision: "approved", topic: { ...draft, name: "Backend engineering" }, note: "Checked scope" }, { headers: { "X-CSRF-Token": "test-csrf" } }));
    await screen.findByRole("link", { name: "Open topic" });
    expect(screen.queryByRole("button", { name: "Approve and create topic" })).toBeNull();
  });
  it("rejects without applying topic fields", async () => {
    vi.mocked(api.adminTopicProposalReview).mockResolvedValue({ ...proposal, status: "rejected", reviewed_by: { subject: "reviewer" }, reviewed_at: "2026-09-07T01:00:00Z" });
    mount(<TopicProposalReview id="proposal-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject proposal" }));
    await waitFor(() => expect(api.adminTopicProposalReview).toHaveBeenCalledWith("proposal-1", { decision: "rejected", note: null }, { headers: { "X-CSRF-Token": "test-csrf" } }));
  });
  it("requires selecting evidence-backed enrichment terms before proposing them", async () => {
    mount(<TopicEnrichment id="topic-1" />);
    const submit = await screen.findByRole("button", { name: "Send selected keywords for review" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("link", { name: "Database design" }).getAttribute("href")).toBe("/content/articles/article-1");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(submit);
    await waitFor(() => expect(api.adminTopicEnrichmentSubmit).toHaveBeenCalledWith("topic-1", { preview_token: "evidence-token", keywords: ["postgres"] }, { headers: { "X-CSRF-Token": "test-csrf" } }));
    expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
  });
});


it("pulls the GitHub repository with one button and continues batches automatically", async () => {
  vi.mocked(api.adminTopicGithubPull).mockResolvedValueOnce({ revision: "a".repeat(40), total: 102, processed: 100, created: 98, skipped: 2, issues: [], next_offset: 100 }).mockResolvedValueOnce({ revision: "a".repeat(40), total: 102, processed: 102, created: 2, skipped: 0, issues: [], next_offset: null });
  mount(<TopicProposals />);
  fireEvent.click(screen.getByRole("button", { name: "Discover topics" }));
  expect(screen.queryByRole("textbox", { name: "GitHub topic search" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Pull from GitHub" }));
  await waitFor(() => expect(api.adminTopicGithubPull).toHaveBeenCalledTimes(2));
  expect(api.adminTopicGithubPull).toHaveBeenNthCalledWith(1, {}, { headers: { "X-CSRF-Token": "test-csrf" } });
  expect(api.adminTopicGithubPull).toHaveBeenNthCalledWith(2, { revision: "a".repeat(40), offset: 100 }, { headers: { "X-CSRF-Token": "test-csrf" } });
  await screen.findByRole("link", { name: "Review imported topics" });
  expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
});

it("can continue after a GitHub pull fails without asking for search parameters", async () => {
  vi.mocked(api.adminTopicGithubPull).mockRejectedValueOnce(new Error("GitHub temporarily unavailable"));
  mount(<TopicProposals />);
  fireEvent.click(screen.getByRole("button", { name: "Discover topics" }));
  fireEvent.click(screen.getByRole("button", { name: "Pull from GitHub" }));
  fireEvent.click(await screen.findByRole("button", { name: "Continue pulling" }));
  await screen.findByRole("link", { name: "Review imported topics" });
  expect(api.adminTopicGithubPull).toHaveBeenCalledTimes(2);
});


describe("proposal table", () => {
  it("shows a scannable table with review links and keeps discovery collapsed", async () => {
    vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [proposal], total: 31, offset: 0, limit: 25 });
    mount(<TopicProposals />);
    const table = screen.getByRole("table", { name: "Topic proposals" });
    await within(table).findByRole("link", { name: "Review Backend" });
    expect(within(table).getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["", "Topic", "Kind", "Change", "Keywords", "Source", "Status", "AI analysis", "Submitted", "Actions"]);
    expect(within(table).getByRole("cell", { name: "New topic" })).toBeDefined();
    expect(within(table).getByText("topics.json")).toBeDefined();
    expect(within(table).getByRole("link", { name: "Review Backend" }).getAttribute("href")).toBe("/taxonomy/topics/proposals/proposal-1");
    expect(screen.queryByRole("button", { name: "Pull from GitHub" })).toBeNull();
    expect(screen.getByText("1–1 of 31")).toBeDefined();
    expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
  });

  it("preserves batch, query, and page size when changing status, sort, or page", async () => {
    router.query = "status=pending&batch_id=batch-1&q=backend&offset=25&limit=25&sort=-created_at";
    vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [proposal], total: 80, offset: 25, limit: 25 });
    mount(<TopicProposals />);
    await screen.findByRole("link", { name: "Review Backend" });
    expect(api.adminTopicProposalsList).toHaveBeenCalledWith({ status: "pending", batch_id: "batch-1", q: "backend", offset: 25, limit: 25, sort: "-created_at" }, expect.anything());
    const approved = within(screen.getByRole("navigation", { name: "Proposal status" })).getByRole("link", { name: "Approved" });
    expect(approved.getAttribute("href")).toBe("/taxonomy/topics/proposals?status=approved&batch_id=batch-1&q=backend&offset=0&limit=25&sort=-created_at");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?status=pending&batch_id=batch-1&q=backend&offset=50&limit=25&sort=-created_at", { scroll: false });
    fireEvent.click(screen.getByRole("button", { name: "Sort by topic slug" }));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?status=pending&batch_id=batch-1&q=backend&offset=0&limit=25&sort=slug", { scroll: false });
    fireEvent.change(screen.getByRole("textbox", { name: "Search proposals" }), { target: { value: "github" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?status=pending&batch_id=batch-1&q=github&offset=0&limit=25&sort=-created_at", { scroll: false });
  });

  it("restores combined filters from the URL and preserves them across navigation", async () => {
    router.query = "status=pending&kind=technology&source=GitHub+curated+topics&action=create&analysis=failed&missing=keywords&offset=25&limit=25";
    vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [proposal], total: 80, offset: 25, limit: 25 });
    mount(<TopicProposals />);
    await screen.findByRole("link", { name: "Review Backend" });
    expect(api.adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ kind: "technology", source: "GitHub curated topics", action: "create", analysis: "failed", missing: "keywords", offset: 25 }), expect.anything());
    expect(screen.getByRole("combobox", { name: "AI analysis filter" }).textContent).toBe("Failed");
    const approved = within(screen.getByRole("navigation", { name: "Proposal status" })).getByRole("link", { name: "Approved" });
    const next = new URL(approved.getAttribute("href")!, "http://example.test").searchParams;
    expect(next.get("analysis")).toBe("failed"); expect(next.get("missing")).toBe("keywords"); expect(next.get("offset")).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Remove source filter" }));
    const removed = new URL(router.push.mock.lastCall![0], "http://example.test").searchParams;
    expect(removed.has("source")).toBe(false); expect(removed.get("kind")).toBe("technology"); expect(removed.get("offset")).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?status=pending&offset=0&limit=25", { scroll: false });
  });

  it("offers catalog-wide choices and resets pagination when adding a filter", async () => {
    router.query = "q=backend&offset=50&limit=25";
    mount(<TopicProposals />);
    await waitFor(() => expect(api.adminTopicProposalFilterOptions).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Kind" }));
    fireEvent.click(await screen.findByRole("option", { name: "Language" }));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?q=backend&offset=0&limit=25&kind=language", { scroll: false });
    fireEvent.click(screen.getByRole("button", { name: "Close filters" }));
    fireEvent.click(screen.getByRole("combobox", { name: "AI analysis filter" }));
    fireEvent.click(screen.getByRole("option", { name: "Ready for review" }));
    expect(router.push).toHaveBeenLastCalledWith("/taxonomy/topics/proposals?q=backend&offset=0&limit=25&analysis=enriched", { scroll: false });
  });

  it("uses view actions for reviewed proposals and recovers from a failed table request", async () => {
    router.query = "status=rejected";
    vi.mocked(api.adminTopicProposalsList).mockRejectedValueOnce(new Error("Temporarily unavailable"))
      .mockResolvedValue({ items: [{ ...proposal, status: "rejected" }], total: 1, offset: 0, limit: 25 });
    mount(<TopicProposals />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByRole("link", { name: "View Backend" });
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("explains an empty search and clears its filters without changing the status", async () => {
    router.query = "status=approved&q=missing&batch_id=batch-1&offset=25";
    mount(<TopicProposals />);
    const table = screen.getByRole("table", { name: "Topic proposals" });
    await within(table).findByText("No proposals match these filters");
    fireEvent.click(within(table).getByRole("button", { name: "Clear filters" }));
    expect(router.push).toHaveBeenCalledWith("/taxonomy/topics/proposals?status=approved&offset=0", { scroll: false });
  });
});

it("keeps a GitHub pull running when discovery is closed and restores progress on reopening", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.adminTopicGithubPull>>) => void;
  vi.mocked(api.adminTopicGithubPull).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  mount(<TopicProposals />);
  fireEvent.click(screen.getByRole("button", { name: "Discover topics" }));
  fireEvent.click(screen.getByRole("button", { name: "Pull from GitHub" }));
  fireEvent.click(screen.getByRole("button", { name: "Discover topics" }));
  expect(screen.queryByRole("button", { name: "Continue pulling" })).toBeNull();
  finish({ revision: "a".repeat(40), total: 1, processed: 1, created: 1, skipped: 0, issues: [], next_offset: null });
  await waitFor(() => expect(screen.getByRole("button", { name: "Discover topics" }).textContent).toBe("Discover topics"));
  fireEvent.click(screen.getByRole("button", { name: "Discover topics" }));
  expect(screen.getByText("Checked 1 of 1 topics. 1 new proposals.")).toBeDefined();
  expect(api.adminTopicGithubPull).toHaveBeenCalledTimes(1);
  expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
});


it("shows review context and lets admins choose columns without losing choices on refresh", async () => {
  vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [{ ...proposal,
    proposed: { ...draft, aliases: ["Server-side"], keywords: ["api", "backend", "systems"] },
    evidence: [{ article_id: "article-1", quote: "Backend engineering" }],
  }], total: 1, offset: 0, limit: 25 });
  mount(<TopicProposals />);
  const table = screen.getByRole("table", { name: "Topic proposals" });
  await within(table).findByRole("link", { name: "Review Backend" });
  expect(within(table).getByText("Discipline")).toBeDefined();
  expect(within(table).getByLabelText("3 keywords: api, backend, systems")).toBeDefined();
  expect(within(table).getByText("+1")).toBeDefined();
  expect(within(table).getByText("Server engineering")).toBeDefined();
  expect(within(table).getByRole("link", { name: "1 article" }).getAttribute("href")).toBe("/content/articles/article-1");
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  expect((screen.getByRole("checkbox", { name: "Topic" }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("checkbox", { name: "Actions" }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Aliases" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Description" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Keywords" }));
  expect(within(table).getByRole("columnheader", { name: "Aliases" })).toBeDefined();
  expect(within(table).getByRole("columnheader", { name: "Description" })).toBeDefined();
  expect(within(table).getByText("Server-side")).toBeDefined();
  expect(within(table).queryByRole("columnheader", { name: "Keywords" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await within(table).findByText("Server-side");
  expect(within(table).queryByRole("columnheader", { name: "Keywords" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("button", { name: "Reset columns" }));
  expect(within(table).getByRole("columnheader", { name: "Keywords" })).toBeDefined();
  expect(within(table).queryByRole("columnheader", { name: "Aliases" })).toBeNull();
});


describe("proposal row actions and AI research", () => {
  it("approves the displayed version with CSRF and prevents repeated clicks", async () => {
    const current = { ...proposal, content_hash: "a".repeat(64) };
    vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [current], total: 1, offset: 0, limit: 25 });
    let finish!: (value: TopicProposalOut) => void;
    vi.mocked(api.adminTopicProposalReview).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    mount(<TopicProposals />);
    const approve = await screen.findByRole("button", { name: "Approve Backend" });
    fireEvent.click(approve); fireEvent.click(approve);
    expect(api.adminTopicProposalReview).toHaveBeenCalledTimes(1);
    expect(api.adminTopicProposalReview).toHaveBeenCalledWith("proposal-1", { decision: "approved", topic: draft, expected_input_hash: "a".repeat(64) }, { headers: { "X-CSRF-Token": "test-csrf" } });
    expect((screen.getByRole("button", { name: "Reject Backend" }) as HTMLButtonElement).disabled).toBe(true);
    finish({ ...current, status: "approved" });
    await waitFor(() => expect(api.adminTopicProposalsList).toHaveBeenCalledTimes(2));
  });
  it("rejects a row without applying fields and leaves retry available on failure", async () => {
    vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [proposal], total: 1, offset: 0, limit: 25 });
    vi.mocked(api.adminTopicProposalReview).mockRejectedValue(new Error("Proposal changed. Reload it before reviewing"));
    mount(<TopicProposals />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject Backend" }));
    await waitFor(() => expect(notifyFailure).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.adminTopicProposalReview).toHaveBeenCalledWith("proposal-1", { decision: "rejected" }, { headers: { "X-CSRF-Token": "test-csrf" } });
    expect((screen.getByRole("button", { name: "Reject Backend" }) as HTMLButtonElement).disabled).toBe(false);
  });
  it("runs web research from the review page, refreshes fields and shows sources without approving", async () => {
    const enriched: TopicProposalOut = { ...proposal, content_hash: "b".repeat(64), proposed: { ...draft, aliases: ["Server development"] }, evidence: [{ provider: "ai_topic_research", fields: ["aliases"], sources: [{ url: "https://example.com/project", title: "Project documentation", quote: "Server development is its alternate name." }] }], analysis: { id: "analysis-1", status: "succeeded", attempts: 1, model: "configured-model", created_at: proposal.created_at, finished_at: proposal.created_at, error: null, outcome: "enriched" } };
    vi.mocked(api.adminTopicProposalGet).mockResolvedValueOnce(proposal).mockResolvedValue(enriched);
    vi.mocked(api.adminTopicProposalAnalyze).mockResolvedValue({ id: "analysis-1", kind: "topic-analysis", status: "queued", attempts: 0, created_at: proposal.created_at, available_at: proposal.created_at, finished_at: null, error: null, details: {} });
    mount(<TopicProposalReview id="proposal-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Run AI analysis for Backend" }));
    await waitFor(() => expect(api.adminTopicProposalAnalyze).toHaveBeenCalledWith("proposal-1", { headers: { "X-CSRF-Token": "test-csrf" } }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Approve and create topic" }) as HTMLButtonElement).disabled).toBe(true));
    await screen.findByRole("link", { name: "Project documentation" }, { timeout: 3500 });
    expect((screen.getByRole("textbox", { name: /^Aliases/ }) as HTMLTextAreaElement).value).toBe("Server development");
    expect(api.adminTopicProposalReview).not.toHaveBeenCalled();
    await waitFor(() => expect((screen.getByRole("button", { name: "Approve and create topic" }) as HTMLButtonElement).disabled).toBe(false));
  });
  it("preserves unsaved edits by disabling analysis until they are resolved", async () => {
    mount(<TopicProposalReview id="proposal-1" />);
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Human change" } });
    expect((screen.getByRole("button", { name: "Run AI analysis for Backend" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.adminTopicProposalAnalyze).not.toHaveBeenCalled();
  });
});


it("keeps a failed AI status check inside one icon and retries without rerunning analysis", async () => {
  const running: TopicProposalOut = { ...proposal, analysis: { id: "analysis-1", status: "running", attempts: 1, model: "model", created_at: proposal.created_at, finished_at: null, error: null, outcome: null } };
  vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [running], total: 1, offset: 0, limit: 25 });
  vi.mocked(api.adminTopicProposalGet).mockRejectedValueOnce(new Error("The admin service is unavailable. Try again.")).mockResolvedValue({ ...running, analysis: { ...running.analysis!, status: "succeeded", outcome: "enriched" } });
  mount(<TopicProposals />);
  const group = await screen.findByRole("group", { name: "Actions for Backend" });
  const retry = await within(group).findByRole("button", { name: "Retry AI status for Backend" }, { timeout: 3500 });
  expect(group.querySelectorAll("svg").length).toBe(4);
  expect(group.querySelector('[role="alert"]')).toBeNull();
  expect(within(group).queryByText(/admin service is unavailable/)).toBeNull();
  fireEvent.click(retry);
  await waitFor(() => expect(api.adminTopicProposalGet).toHaveBeenCalledTimes(2), { timeout: 3500 });
  expect(api.adminTopicProposalAnalyze).not.toHaveBeenCalled();
});


it("shows human attribution in provenance and table columns without raw IDs", async () => {
  const value: TopicProposalOut = { ...proposal, created_by: { subject: "389598389664220144", name: "Alex Morgan" }, status: "approved", reviewed_at: proposal.created_at, reviewed_by: { subject: "99334455", email: "reviewer@example.com" } };
  vi.mocked(api.adminTopicProposalGet).mockResolvedValue(value);
  const detail = mount(<TopicProposalReview id="proposal-1" />);
  expect(await screen.findByText(/Submitted by Alex Morgan/)).toBeDefined();
  expect(screen.getByText(/Reviewed by reviewer@example.com/)).toBeDefined();
  expect(screen.queryByText(/389598389664220144|99334455/)).toBeNull();
  detail.unmount();
  vi.mocked(api.adminTopicProposalsList).mockResolvedValue({ items: [value], total: 1, offset: 0, limit: 25 });
  mount(<TopicProposals />);
  await screen.findByRole("link", { name: "View Backend" });
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Submitted by" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Reviewed by" }));
  const table = screen.getByRole("table", { name: "Topic proposals" });
  expect(within(table).getByText("Alex Morgan")).toBeDefined();
  expect(within(table).getByText("reviewer@example.com")).toBeDefined();
  expect(table.textContent).not.toMatch(/389598389664220144|99334455/);
});
