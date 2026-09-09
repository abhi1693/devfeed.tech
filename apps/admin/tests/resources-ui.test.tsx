// @vitest-environment jsdom
import { renderAdmin } from "./render-admin";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminSession } from "@/components/molecules/admin-session";
import { ResourceForm } from "@/components/organisms/resource-form";
import { ResourceDelete } from "@/components/organisms/resource-delete";
import { ResourceList } from "@/components/organisms/resource-list";
import { RecordTable } from "@/components/organisms/record-table";
import { Sidebar } from "@/components/organisms/sidebar";
import { ApiError } from "@/lib/api/client";
import { listRecords, getRecord, saveRecord, deleteRecord } from "@/lib/resource-api";
import { formPayload, initialValues } from "@/lib/form-values";
import { resources, resourceKeys, resourceHref } from "@/lib/resources";
import { toast } from "sonner";
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams("q=React&offset=25&limit=25"), usePathname: () => "/taxonomy/topics/test/edit" }));
vi.mock("@/lib/resource-api", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/resource-api")>(), listRecords: vi.fn(), getRecord: vi.fn(), saveRecord: vi.fn(), deleteRecord: vi.fn() }));
const topic = { id: "topic-1", name: "Languages", slug: "languages", keywords: ["code"], kind: "discipline", status: "active", aliases: [], website_url: null, logo_url: null };
function withAdmin(children: React.ReactNode) { return render(<AdminSession admin={{ subject: "admin", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }}>{children}</AdminSession>); }
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.mocked(getRecord).mockResolvedValue(topic);
  vi.mocked(saveRecord).mockResolvedValue(topic);
  vi.mocked(deleteRecord).mockResolvedValue(undefined);
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
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(router.push).toHaveBeenCalledWith("/taxonomy/topics?q=Terraform&offset=0&limit=25", { scroll: false });
  });
  it("does not offer create/edit/delete for worker-owned jobs", async () => {
    renderAdmin(<ResourceList resource="analysis-jobs" />);
    await screen.findByText("No analysis runs match these filters.");
    expect(screen.queryByRole("link", { name: /^Add/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Delete" })).toBeNull();
  });
});

describe("dedicated object forms", () => {
  it.each([
    { id: undefined, label: "Create topic", cancelHref: "/taxonomy/topics" },
    { id: "topic-1", label: "Save changes", cancelHref: "/taxonomy/topics/topic-1" },
  ])("right-aligns form actions with $label after Cancel", async ({ id, label, cancelHref }) => {
    withAdmin(<ResourceForm resource="topics" id={id} />);
    const submit = await screen.findByRole("button", { name: label });
    const cancel = screen.getByRole("link", { name: "Cancel" });
    expect(submit.parentElement!.className).toContain("justify-end");
    expect(Array.from(submit.parentElement!.children)).toEqual([cancel, submit]);
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
    vi.mocked(deleteRecord).mockRejectedValue(new ApiError(409, "Cannot delete: linked tags exist."));
    withAdmin(<ResourceDelete resource="topics" id="topic-1" />);
    const button = await screen.findByRole("button", { name: "Delete permanently" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
    fireEvent.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not delete topic", expect.objectContaining({ description: "Cannot delete: linked tags exist." })));
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
  it("preserves publication timestamp precision when an unrelated field is edited", () => {
    const record = { id: "article-1", title: "Article", published_at: "2026-01-01T00:00:12.123456Z", editorial_revision: 7 };
    const payload = formPayload("articles", initialValues("articles", record), record);
    expect(payload.published_at).toBe(record.published_at);
    expect(payload.expected_revision).toBe(7);
    expect(payload).not.toHaveProperty("canonical_url");
    expect(payload).not.toHaveProperty("ai_summary");
  });
});
