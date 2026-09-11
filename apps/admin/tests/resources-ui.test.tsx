// @vitest-environment jsdom
import { renderAdmin } from "./render-admin";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminSession } from "@/components/molecules/admin-session";
import { ResourceForm } from "@/components/organisms/resource-form";
import { ResourceDelete } from "@/components/organisms/resource-delete";
import { ResourceList } from "@/components/organisms/resource-list";
import { RecordTable } from "@/components/organisms/record-table";
import { Sidebar } from "@/components/organisms/sidebar";
import { ApiError } from "@/lib/api/client";
import { listRecords, getRecord, saveRecord, deleteRecord } from "@/lib/resource-api";
import { adminTopicDeletePreview, adminTopicReplacementsList, adminTopicProposalGet, adminTopicProposalsList, adminTopicProposalFilterOptions } from "@/lib/api/generated/admin";
import { formPayload, initialValues } from "@/lib/form-values";
import { resources, resourceKeys, resourceHref } from "@/lib/resources";
import { toast } from "sonner";
import { normalizeSettings } from "@/lib/settings";
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
const navigation = vi.hoisted(() => ({ query: "q=React&offset=25&limit=25" }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams(navigation.query), usePathname: () => "/taxonomy/topics/test/edit" }));
vi.mock("@/lib/resource-api", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/resource-api")>(), listRecords: vi.fn(), getRecord: vi.fn(), saveRecord: vi.fn(), deleteRecord: vi.fn() }));
vi.mock("@/lib/api/generated/admin", async original => ({ ...await original<typeof import("@/lib/api/generated/admin")>(), adminTopicDeletePreview: vi.fn(), adminTopicReplacementsList: vi.fn(), adminTopicProposalGet: vi.fn(), adminTopicProposalsList: vi.fn(), adminTopicProposalFilterOptions: vi.fn() }));
const topic = { id: "topic-1", name: "Languages", slug: "languages", keywords: ["code"], kind: "discipline", status: "active", aliases: [], website_url: null, logo_url: null };
function withAdmin(children: React.ReactNode) { return render(<AdminSession admin={{ subject: "admin", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }}>{children}</AdminSession>); }
beforeEach(() => {
  vi.clearAllMocks();
  navigation.query = "q=React&offset=25&limit=25";
  vi.mocked(listRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.mocked(getRecord).mockResolvedValue(topic);
  vi.mocked(saveRecord).mockResolvedValue(topic);
  vi.mocked(deleteRecord).mockResolvedValue(undefined);
  vi.mocked(adminTopicDeletePreview).mockResolvedValue({ articles: 3, published_articles: 2, tags: 1, relationships: 2, relationship_proposals: 1, research_jobs: 1, pending_topic_proposals: 1 });
  vi.mocked(adminTopicReplacementsList).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.mocked(adminTopicProposalsList).mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
  vi.mocked(adminTopicProposalFilterOptions).mockResolvedValue({ kinds: [], sources: [] });
});
afterEach(cleanup);

describe("object navigation and table conventions", () => {
  it("groups all supported objects and marks the current sidebar item", () => {
    render(<Sidebar />);
    for (const group of ["Content", "Taxonomy", "Operations"]) expect(screen.getByRole("region", { name: group })).toBeDefined();
    for (const resource of resourceKeys) expect(screen.getByRole("link", { name: resources[resource].label }).getAttribute("href")).toBe(resourceHref(resource));
    expect(screen.getByRole("link", { name: "Topics" }).getAttribute("aria-current")).toBe("page");
    const toggle = screen.getByRole("button", { name: "Navigation" }); fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });
  it("provides real object/action links and server-side pagination", () => {
    const change = vi.fn();
    renderAdmin(<RecordTable resource="topics" page={{ items: [topic], total: 26, limit: 25, offset: 0 }} sort="name" onChange={change} />);
    expect(screen.getByRole("link", { name: "Languages" }).getAttribute("href")).toBe("/taxonomy/topics/topic-1");
    expect(screen.getByRole("link", { name: "Edit" }).getAttribute("href")).toBe("/taxonomy/topics/topic-1/edit");
    expect(screen.getByRole("link", { name: "Delete" }).getAttribute("href")).toBe("/taxonomy/topics/topic-1/delete");
    expect(screen.queryByRole("link", { name: "View" })).toBeNull();
    expect(screen.getByRole("link", { name: "Edit" }).textContent).toBe("");
    expect(screen.getByRole("link", { name: "Delete" }).textContent).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(change).toHaveBeenCalledWith({ offset: "25", limit: "25" });
    fireEvent.click(screen.getByRole("button", { name: "Name" }));
    expect(change).toHaveBeenCalledWith({ sort: "-name", offset: "0" });
  });
  it("keeps navigation usable when a saved offset exceeds the remaining records", () => {
    const change = vi.fn();
    renderAdmin(<RecordTable resource="topics" page={{ items: [], total: 1, limit: 25, offset: 25 }} sort="name" onChange={change} />);
    expect(screen.getByText("0 on this page · 1 total")).toBeDefined();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(change).toHaveBeenCalledWith({ offset: "0", limit: "25" });
  });
  it("omits the actions column on read-only job tables while keeping the run link", () => {
    renderAdmin(<RecordTable resource="ingestion-jobs" page={{ items: [{ id: "job-123456", status: "succeeded", created_at: "2026-09-07T00:00:00Z", attempts: 1 }], total: 1, offset: 0, limit: 25 }} sort="-created_at" onChange={vi.fn()} />);
    expect(screen.queryByRole("columnheader", { name: "Actions" })).toBeNull();
    expect(screen.queryByRole("link", { name: "View" })).toBeNull();
    expect(screen.getByRole("link", { name: "job-1234" }).getAttribute("href")).toBe("/jobs/ingestion/job-123456");
  });
  it("searches and paginates at the API, not just within downloaded rows", async () => {
    renderAdmin(<ResourceList resource="topics" />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledWith("topics", { q: "React", offset: 25, limit: 25, sort: "name" }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByRole("textbox", { name: "Search topics" }), { target: { value: "Terraform" } });
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/taxonomy/topics?q=Terraform&offset=0&limit=25", { scroll: false }));
  });
  it("opens pending proposals from the proposed filter and a visible shortcut", async () => {
    renderAdmin(<ResourceList resource="topics" />);
    expect(within(screen.getByRole("navigation", { name: "Topic views" })).getByRole("link", { name: "Proposed" }).getAttribute("href")).toBe("/taxonomy/topics?q=React&offset=0&limit=25&view=proposals&status=pending");
    fireEvent.click(screen.getByRole("combobox", { name: "Status" }));
    fireEvent.click(await screen.findByRole("option", { name: "Proposed" }));
    expect(router.push).toHaveBeenCalledWith("/taxonomy/topics?q=React&offset=0&limit=25&status=pending&view=proposals", { scroll: false });
  });
  it("renders an explicit proposed filter on the same screen without querying catalog topics", async () => {
    navigation.query = "limit=10&offset=0&status=proposed";
    renderAdmin(<ResourceList resource="topics" />);
    await waitFor(() => expect(adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ status: "pending", limit: 10, offset: 0 }), { signal: expect.any(AbortSignal) }));
    expect(router.replace).not.toHaveBeenCalled();
    expect(listRecords).not.toHaveBeenCalled();
  });
  it("opens proposals when the proposed filter was restored from preferences", async () => {
    navigation.query = "";
    render(<AdminSession admin={{ subject: "admin", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }} settings={normalizeSettings({ tables: { topics: { query: { status: "proposed", limit: "10", sort: "name" } } } })}><ResourceList resource="topics" /></AdminSession>);
    await waitFor(() => expect(adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ status: "pending", limit: 10, sort: "slug" }), { signal: expect.any(AbortSignal) }));
    expect(router.replace).not.toHaveBeenCalled();
    expect(listRecords).not.toHaveBeenCalled();
  });
  it.each(["pending", "approved", "rejected"])("shows %s proposals on the Topics screen without loading the catalog", async status => {
    navigation.query = `view=proposals&status=${status}&limit=10&offset=0&q=React&sort=slug&kind=framework`;
    renderAdmin(<ResourceList resource="topics" />);
    await waitFor(() => expect(adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ status, q: "React", kind: "framework", limit: 10, offset: 0, sort: "slug" }), { signal: expect.any(AbortSignal) }));
    expect(screen.getAllByRole("heading", { level: 1 }).map(node => node.textContent)).toEqual(["Topics"]);
    expect(screen.getByRole("table", { name: "Topic proposals" })).toBeDefined();
    expect(listRecords).not.toHaveBeenCalled();
    const catalog = within(screen.getByRole("navigation", { name: "Topic views" })).getByRole("link", { name: "Topics" });
    expect(catalog.getAttribute("href")).toBe("/taxonomy/topics?limit=10&offset=0&q=React");
  });
  it("restores a saved proposal view directly on the Topics screen", async () => {
    navigation.query = "";
    render(<AdminSession admin={{ subject: "admin", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }} settings={normalizeSettings({ tables: { topics: { query: { view: "proposals", status: "rejected", limit: "10" } } } })}><ResourceList resource="topics" /></AdminSession>);
    await waitFor(() => expect(adminTopicProposalsList).toHaveBeenCalledWith(expect.objectContaining({ status: "rejected", limit: 10 }), { signal: expect.any(AbortSignal) }));
    expect(listRecords).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  });
  it("does not offer create/edit/delete for worker-owned jobs", async () => {
    renderAdmin(<ResourceList resource="analysis-jobs" />);
    await screen.findByText("No analysis runs match these filters.");
    expect(screen.queryByRole("link", { name: /^Add/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Delete" })).toBeNull();
  });
});

describe("dedicated object forms", () => {
  it("preserves automatic tag discovery on edits and makes manual clearing explicit", async () => {
    vi.mocked(getRecord).mockResolvedValue({ id: "tag-1", name: "K8s", slug: "k8s", aliases: [], topic_id: "topic-1", auto_link_topic: true });
    withAdmin(<ResourceForm resource="tags" id="tag-1" />);
    const automatic = await screen.findByRole("checkbox", { name: "Discover topic automatically" });
    const selector = screen.getByRole("combobox", { name: "Topic" }) as HTMLButtonElement;
    expect((automatic as HTMLInputElement).checked).toBe(true);
    expect(selector.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Kubernetes label" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(saveRecord).toHaveBeenCalledWith("tags", expect.objectContaining({ auto_link_topic: true, topic_id: "topic-1" }), "test-csrf", "tag-1"));
    cleanup();
    withAdmin(<ResourceForm resource="tags" id="tag-1" />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Discover topic automatically" }));
    expect((screen.getByRole("combobox", { name: "Topic" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(saveRecord).toHaveBeenLastCalledWith("tags", expect.objectContaining({ auto_link_topic: false, topic_id: "topic-1" }), "test-csrf", "tag-1"));
  });
  it.each([
    { id: undefined, label: "Create topic", cancelHref: "/taxonomy/topics" },
    { id: "topic-1", label: "Save changes", cancelHref: "/taxonomy/topics/topic-1" },
  ])("right-aligns form actions with $label after Cancel", async ({ id, label, cancelHref }) => {
    withAdmin(<ResourceForm resource="topics" id={id} />);
    const submit = await screen.findByRole("button", { name: label });
    const cancel = screen.getByRole("link", { name: "Cancel" });
    expect(submit.parentElement!.className).toContain("justify-end");
    expect(Array.from(submit.parentElement!.children)).toEqual(id ? [cancel, submit] : [cancel, submit, screen.getByRole("button", { name: "Create and add another" })]);
    if (id) expect(screen.queryByRole("button", { name: "Create and add another" })).toBeNull();
    expect(submit.getAttribute("type")).toBe("submit");
    expect(cancel.getAttribute("href")).toBe(cancelHref);
  });
  it.each(["topics", "tags"] as const)("generates a slug while typing a new %s name", async resource => {
    const user = userEvent.setup();
    withAdmin(<ResourceForm resource={resource} />);
    const name = await screen.findByLabelText(/Name/);
    const slug = screen.getByLabelText(/Slug/) as HTMLInputElement;
    await user.type(name, "React");
    expect(slug.value).toBe("react");
    await user.type(name, " Native");
    expect(slug.value).toBe("react-native");
    await user.clear(name);
    expect(slug.value).toBe("");
    expect(saveRecord).not.toHaveBeenCalled();
  });
  it("keeps manual slugs, including edits made before entering a name, until cleared", async () => {
    withAdmin(<ResourceForm resource="tags" />);
    const name = await screen.findByLabelText(/Name/);
    const slug = screen.getByLabelText(/Slug/) as HTMLInputElement;
    fireEvent.change(slug, { target: { value: "custom-tag" } });
    fireEvent.change(name, { target: { value: "JavaScript" } });
    expect(slug.value).toBe("custom-tag");
    fireEvent.change(slug, { target: { value: "" } });
    fireEvent.change(name, { target: { value: "JavaScript Tools" } });
    expect(slug.value).toBe("javascript-tools");
    fireEvent.change(slug, { target: { value: "js-tools" } });
    fireEvent.change(name, { target: { value: "JavaScript Libraries" } });
    expect(slug.value).toBe("js-tools");
    fireEvent.click(screen.getByRole("button", { name: "Create tag" }));
    await waitFor(() => expect(saveRecord).toHaveBeenCalledWith("tags", expect.objectContaining({ name: "JavaScript Libraries", slug: "js-tools" }), "test-csrf", undefined));
  });
  it("submits a topic form with parsed keywords and a session csrf token", async () => {
    withAdmin(<ResourceForm resource="topics" />);
    fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Languages" } });
    expect((screen.getByLabelText(/Slug/) as HTMLInputElement).value).toBe("languages");
    fireEvent.change(screen.getByLabelText("Matching keywords"), { target: { value: "code\n code\npython" } });
    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "discipline" } });
    fireEvent.click(screen.getByRole("button", { name: "Create topic" }));
    await waitFor(() => expect(saveRecord).toHaveBeenCalledWith("topics", { name: "Languages", slug: "languages", description: null, keywords: ["code", "python"], facts: [], kind: "discipline", status: "active", aliases: [], website_url: null, logo_url: null }, "test-csrf", undefined));
    expect(router.replace).toHaveBeenCalledWith("/taxonomy/topics/topic-1");
    expect(toast.success).toHaveBeenCalledWith("Topic created");
    expect(vi.mocked(toast.success).mock.invocationCallOrder[0]).toBeLessThan(router.replace.mock.invocationCallOrder[0]);
  });
  it("preserves a failed form and displays field-level validation", async () => {
    vi.mocked(saveRecord).mockRejectedValue(new ApiError(422, "Please correct the highlighted fields.", { slug: "Invalid slug" }));
    withAdmin(<ResourceForm resource="topics" />);
    fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Languages" } });
    fireEvent.change(screen.getByLabelText(/Slug/), { target: { value: "BAD slug" } });
    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "discipline" } });
    fireEvent.click(screen.getByRole("button", { name: "Create topic" }));
    await screen.findByText("Invalid slug");
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Renamed languages" } });
    expect((screen.getByLabelText(/Slug/) as HTMLInputElement).value).toBe("BAD slug");
    expect(router.replace).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Could not save topic", expect.objectContaining({ description: "Please correct the highlighted fields." }));
    expect(toast.success).not.toHaveBeenCalled();
  });
  it("creates another topic on a fresh form, clearing facts and manual slug state", async () => {
    withAdmin(<ResourceForm resource="topics" />);
    fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "First topic" } });
    fireEvent.change(screen.getByLabelText(/Slug/), { target: { value: "manual-slug" } });
    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "discipline" } });
    fireEvent.click(screen.getByRole("button", { name: "Add fact" }));
    fireEvent.change(screen.getByLabelText(/Fact name/), { target: { value: "Release" } });
    fireEvent.change(screen.getByLabelText(/^Value/), { target: { value: "2026" } });
    fireEvent.change(screen.getByLabelText(/Source URL/), { target: { value: "https://example.com/release" } });
    fireEvent.change(screen.getByLabelText(/Retrieved at/), { target: { value: "2026-09-09T12:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Create and add another" }));
    await waitFor(() => expect((screen.getByLabelText(/Name/) as HTMLInputElement).value).toBe(""));
    expect(saveRecord).toHaveBeenCalledTimes(1);
    expect(saveRecord).toHaveBeenCalledWith("topics", expect.objectContaining({ name: "First topic", slug: "manual-slug", facts: [expect.objectContaining({ name: "Release" })] }), "test-csrf", undefined);
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.refresh).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByLabelText(/Name/));
    expect(screen.queryByLabelText(/Fact name/)).toBeNull();
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Second topic" } });
    expect((screen.getByLabelText(/Slug/) as HTMLInputElement).value).toBe("second-topic");
    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "technology" } });
    fireEvent.click(screen.getByRole("button", { name: "Create topic" }));
    await waitFor(() => expect(saveRecord).toHaveBeenCalledTimes(2));
    expect(saveRecord).toHaveBeenLastCalledWith("topics", expect.objectContaining({ name: "Second topic", slug: "second-topic", facts: [] }), "test-csrf", undefined);
    expect(router.replace).toHaveBeenCalledWith("/taxonomy/topics/topic-1");
  });
  it("keeps failed create-and-add-another input and prevents a second save while pending", async () => {
    let reject!: (reason: unknown) => void;
    vi.mocked(saveRecord).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    withAdmin(<ResourceForm resource="tags" />);
    fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "JavaScript" } });
    const another = screen.getByRole("button", { name: "Create and add another" });
    fireEvent.click(another);
    expect((screen.getByRole("button", { name: "Create tag" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(another);
    expect(saveRecord).toHaveBeenCalledTimes(1);
    reject(new ApiError(409, "This tag already exists"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not save tag", expect.objectContaining({ description: "This tag already exists" })));
    expect((screen.getByLabelText(/Name/) as HTMLInputElement).value).toBe("JavaScript");
    expect((screen.getByLabelText(/Slug/) as HTMLInputElement).value).toBe("javascript");
    expect(router.replace).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Create and add another" }) as HTMLButtonElement).disabled).toBe(false);
  });
  it("keeps ordinary Create as the default when submitting with Enter", async () => {
    const user = userEvent.setup();
    withAdmin(<ResourceForm resource="tags" />);
    await user.type(await screen.findByLabelText(/Name/), "JavaScript{Enter}");
    await waitFor(() => expect(saveRecord).toHaveBeenCalledTimes(1));
    expect(router.replace).toHaveBeenCalledWith("/taxonomy/tags/topic-1");
  });
  it("loads existing object values on the edit page", async () => {
    withAdmin(<ResourceForm resource="topics" id="topic-1" />);
    const name = await screen.findByLabelText(/Name/) as HTMLInputElement;
    expect(name.value).toBe("Languages");
    fireEvent.change(name, { target: { value: "Programming languages" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect((screen.getByLabelText(/Slug/) as HTMLInputElement).value).toBe("languages");
    await waitFor(() => expect(saveRecord).toHaveBeenCalledWith("topics", expect.objectContaining({ name: "Programming languages", slug: "languages" }), "test-csrf", "topic-1"));
    expect(toast.success).toHaveBeenCalledWith("Topic updated");
  });
  it("requires explicit delete confirmation and keeps conflicts visible", async () => {
    vi.mocked(deleteRecord).mockRejectedValue(new ApiError(409, "Linked records are being updated. Try again."));
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    const button = await screen.findByRole("button", { name: "Delete permanently" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
    fireEvent.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not delete topic", expect.objectContaining({ description: "Linked records are being updated. Try again." })));
    expect(deleteRecord).toHaveBeenCalledWith("topics", "topic-1", "test-csrf");
    expect(router.replace).not.toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Type DELETE to confirm" }) as HTMLInputElement).value).toBe("DELETE");
    expect(button.disabled).toBe(false);
    expect(toast.success).not.toHaveBeenCalled();
  });
  it("confirms deletion only after the server succeeds", async () => {
    let resolve!: () => void;
    vi.mocked(deleteRecord).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(toast.success).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    resolve();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Topic deleted"));
    expect(router.replace).toHaveBeenCalledWith("/taxonomy/topics");
  });
  it.each(["proposed", "rejected", "active"])("shows deletion impact and can relink to a %s topic", async status => {
    const replacement = { ...topic, id: "replacement", name: "Programming", slug: "programming", status, topic_id: "replacement", proposal_id: null };
    vi.mocked(adminTopicReplacementsList).mockResolvedValue({ items: [replacement], total: 1, limit: 25, offset: 0 });
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    const table = await screen.findByRole("table", { name: "Deletion impact" });
    expect(within(table).getByRole("row", { name: "Linked articles 3 Unpublish and unlink" })).toBeDefined();
    expect(within(table).getByRole("row", { name: "Currently published 2 Remove from public feed" })).toBeDefined();
    expect(adminTopicDeletePreview).toHaveBeenCalledWith("topic-1", { signal: expect.any(AbortSignal) });
    fireEvent.click(screen.getByRole("combobox", { name: "Replacement topic" }));
    const option = await screen.findByRole("option", { name: /Programming/ });
    expect(screen.queryByRole("option", { name: /Languages/ })).toBeNull();
    expect(adminTopicReplacementsList).toHaveBeenCalledWith(expect.objectContaining({ exclude_topic_id: "topic-1" }), { signal: expect.any(AbortSignal) });
    fireEvent.click(option);
    expect(within(table).getByRole("row", { name: "Linked articles 3 Unpublish and relink" })).toBeDefined();
    fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(deleteRecord).toHaveBeenCalledWith("topics", "topic-1", "test-csrf", "replacement"));
  });
  it("can relink to a pending proposal while keeping approval explicit", async () => {
    vi.mocked(adminTopicReplacementsList).mockResolvedValue({ items: [{ id: "proposal:pending-1", name: "Vercel", slug: "vercel", kind: "platform", status: "pending", topic_id: null, proposal_id: "pending-1" }], total: 1, limit: 25, offset: 0 });
    vi.mocked(adminTopicProposalGet).mockResolvedValue({ id: "pending-1", status: "pending", proposed: { name: "Vercel", slug: "vercel", kind: "platform" } } as Awaited<ReturnType<typeof adminTopicProposalGet>>);
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    fireEvent.click(await screen.findByRole("combobox", { name: "Replacement topic" }));
    fireEvent.click(await screen.findByRole("option", { name: /Vercel.*Pending/ }));
    expect(screen.getByText(/Pending proposals stay inactive until approved/)).toBeDefined();
    fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(deleteRecord).toHaveBeenCalledWith("topics", "topic-1", "test-csrf", "proposal:pending-1"));
  });
  it("omits zero counts from the deletion summary", async () => {
    vi.mocked(adminTopicDeletePreview).mockResolvedValue({ articles: 2, published_articles: 0, tags: 0, relationships: 0, relationship_proposals: 1, research_jobs: 0, pending_topic_proposals: 0 });
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    const table = await screen.findByRole("table", { name: "Deletion impact" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByRole("row", { name: "Linked articles 2 Unpublish and unlink" })).toBeDefined();
    expect(within(table).getByRole("row", { name: "Relationship proposals 1 Delete" })).toBeDefined();
    for (const label of ["Currently published", "Tag links", "Relationships", "Relationship research runs", "Pending topic proposals"]) expect(within(table).queryByText(label)).toBeNull();
  });
  it("omits the impact table when there are no affected records", async () => {
    vi.mocked(adminTopicDeletePreview).mockResolvedValue({ articles: 0, published_articles: 0, tags: 0, relationships: 0, relationship_proposals: 0, research_jobs: 0, pending_topic_proposals: 0 });
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    await screen.findByRole("textbox", { name: "Type DELETE to confirm" });
    expect(screen.queryByRole("table", { name: "Deletion impact" })).toBeNull();
  });
  it("does not offer deletion when its impact cannot be loaded", async () => {
    vi.mocked(adminTopicDeletePreview).mockRejectedValueOnce(new ApiError(503, "Unavailable"));
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Delete permanently" })).toBeNull();
    expect(deleteRecord).not.toHaveBeenCalled();
  });
  it("preserves publication timestamp precision when an unrelated field is edited", () => {
    const record = { id: "article-1", title: "Article", published_at: "2026-01-01T00:00:12.123456Z", editorial_revision: 7 };
    const payload = formPayload("articles", initialValues("articles", record), record);
    expect(payload.published_at).toBe(record.published_at);
    expect(payload.expected_revision).toBe(7);
    expect(payload).not.toHaveProperty("canonical_url");
    expect(payload).not.toHaveProperty("ai_summary");
  });
});
