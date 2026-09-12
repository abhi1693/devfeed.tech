// @vitest-environment jsdom
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { notify, notifyFailure } from "@/lib/notifications";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { useRequest } from "@/lib/use-request";
import { AdminSession } from "@/components/molecules/admin-session";
import { LoginPanel } from "@/components/organisms/login-panel";
import { UserMenu } from "@/components/molecules/user-menu";
import { ResourceWorkflow } from "@/components/organisms/resource-workflow";
import { Overview } from "@/components/organisms/overview";
import {
  adminArticleClassify,
  adminArticleReview,
  adminAuthLogout,
  adminOverview,
  adminSourceFetch,
  adminSourceReview,
} from "@/lib/api/generated/admin";
import { getRecord, listRecords } from "@/lib/resource-api";

const router = vi.hoisted(() => ({ replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("@/lib/api/client", async (original) => ({
  ...(await original<typeof import("@/lib/api/client")>()),
  returnToLogin: vi.fn(),
}));
vi.mock("@/lib/resource-api", () => ({ getRecord: vi.fn(), listRecords: vi.fn() }));
vi.mock("@/lib/api/generated/admin", () => ({
  adminArticleReview: vi.fn(),
  adminSourceReview: vi.fn(),
  adminSourceFetch: vi.fn(),
  adminArticleClassify: vi.fn(),
  adminAuthLogout: vi.fn(),
  adminOverview: vi.fn(),
}));
const article = {
  id: "article-1",
  title: "React guide",
  editorial_revision: 1,
  review_status: "pending",
  publication_status: "unpublished",
  publication_blockers: [],
  language: "en",
  content_type: "tutorial",
  content_format: "article",
  topics: [],
  tags: [],
};
const admin = {
  subject: "admin",
  name: "Alex Morgan",
  email: "alex@example.com",
  issuer: "https://identity.example",
  organization_id: "org",
  roles: ["superuser"],
  expires_at: 4102444800,
  csrf_token: "test-csrf",
};
import { emptyOverview as overview } from "./fixtures/overview";
function withAdmin(children: ReactNode) {
  return render(
    <AdminSession
      admin={{
        subject: "admin",
        issuer: "https://identity.example",
        organization_id: "org",
        roles: ["superuser"],
        expires_at: 4102444800,
        csrf_token: "test-csrf",
      }}
    >
      {children}
    </AdminSession>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getRecord).mockResolvedValue(article);
  vi.mocked(listRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
});
afterEach(cleanup);

describe("notification policy", () => {
  it("supports all severities, with more reading time for warnings and errors", () => {
    notify.success("Saved");
    notify.info("Information");
    notify.warning("Warning");
    notify.error("Error");
    expect(toast.success).toHaveBeenCalledWith("Saved");
    expect(toast.info).toHaveBeenCalledWith("Information");
    expect(toast.warning).toHaveBeenCalledWith("Warning", { duration: 8000 });
    expect(toast.error).toHaveBeenCalledWith("Error", { duration: 8000 });
  });
  it("shows actionable API errors, but not arbitrary network or exception details", () => {
    notifyFailure(
      new ApiError(409, "Another editor changed this record. Reload and try again."),
      "Could not save",
    );
    expect(toast.error).toHaveBeenLastCalledWith(
      "Could not save",
      expect.objectContaining({
        description: "Another editor changed this record. Reload and try again.",
      }),
    );
    notifyFailure(new Error("internal-secret-url?token=private"), "Could not save");
    expect(vi.mocked(toast.error).mock.lastCall?.[1]?.description).not.toContain("private");
  });
  it("does not toast cancellations or prevent session-expiry recovery", () => {
    notifyFailure(new DOMException("Aborted", "AbortError"), "Request failed");
    notifyFailure(new ApiError(401), "Request failed");
    expect(toast.error).not.toHaveBeenCalled();
    expect(returnToLogin).toHaveBeenCalledOnce();
  });
  it("deduplicates failed reads until recovery, without hiding later session expiry", async () => {
    const load = vi.fn().mockRejectedValue(new ApiError(503));
    const view = renderHook(({ id }) => useRequest(id, load), { initialProps: { id: "1" } });
    await waitFor(() => expect(view.result.current.error).toBeDefined());
    view.rerender({ id: "2" });
    await waitFor(() => expect(view.result.current.error).toBeDefined());
    expect(toast.error).toHaveBeenCalledTimes(1);
    load.mockRejectedValueOnce(new ApiError(401));
    view.rerender({ id: "3" });
    await waitFor(() => expect(returnToLogin).toHaveBeenCalledOnce());
    load.mockResolvedValueOnce("loaded");
    view.rerender({ id: "4" });
    await waitFor(() => expect(view.result.current.data).toBe("loaded"));
    view.rerender({ id: "5" });
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(2));
  });
  it("ignores stale failures after unmount", async () => {
    let reject!: (error: Error) => void;
    const load = vi.fn(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    );
    const view = renderHook(() => useRequest("record", load));
    view.unmount();
    await act(async () => {
      reject(new ApiError(503));
    });
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe("authentication feedback", () => {
  it("confirms logout once after document navigation, even in Strict Mode", async () => {
    render(
      <StrictMode>
        <LoginPanel enabled signedOut />
      </StrictMode>,
    );
    await waitFor(() => expect(toast.info).toHaveBeenCalledTimes(1));
    expect(toast.info).toHaveBeenCalledWith("You have signed out of DevFeed.", {
      id: "auth-result",
    });
  });
  it("keeps role-denial recovery visible as well as notifying, with no false logout success", async () => {
    render(
      <StrictMode>
        <LoginPanel enabled signedOut error="Required role missing." />
      </StrictMode>,
    );
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.info).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("Required role missing.");
    expect(screen.getByRole("link", { name: "Sign in again" }).getAttribute("href")).toContain(
      "reauthenticate=true",
    );
  });
  it("toasts failed logout and allows retry", async () => {
    const user = userEvent.setup();
    vi.mocked(adminAuthLogout).mockRejectedValueOnce(new ApiError(503));
    render(<UserMenu admin={admin} />);
    await user.click(screen.getByRole("button", { name: "User menu: Alex Morgan" }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not sign out", expect.any(Object)),
    );
    expect(
      screen.getByRole("menuitem", { name: "Sign out" }).getAttribute("aria-disabled"),
    ).not.toBe("true");
    expect(returnToLogin).not.toHaveBeenCalled();
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() => expect(returnToLogin).toHaveBeenCalledWith(true));
  });
  it("opens the account menu with the keyboard and restores focus when dismissed", async () => {
    const user = userEvent.setup();
    render(<UserMenu admin={admin} />);
    await user.tab();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menu", { name: "User menu: Alex Morgan" })).toBeTruthy();
    expect(screen.getByText("alex@example.com")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Settings" }));
    expect(screen.getByRole("menuitem", { name: "Settings" }).getAttribute("href")).toBe(
      "/settings",
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "User menu: Alex Morgan" }),
    );
    expect(adminAuthLogout).not.toHaveBeenCalled();
  });
  it("keeps pending logout disabled across menu closes and sends the CSRF token", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    vi.mocked(adminAuthLogout).mockImplementationOnce(
      () =>
        new Promise<void>((done) => {
          resolve = done;
        }),
    );
    render(<UserMenu admin={admin} />);
    const trigger = screen.getByRole("button", { name: "User menu: Alex Morgan" });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(adminAuthLogout).toHaveBeenCalledExactlyOnceWith({
      headers: { "X-CSRF-Token": "test-csrf" },
    });
    expect(returnToLogin).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    await user.click(trigger);
    expect(
      screen.getByRole("menuitem", { name: "Signing out…" }).getAttribute("aria-disabled"),
    ).toBe("true");
    await act(async () => {
      resolve();
    });
    expect(returnToLogin).toHaveBeenCalledExactlyOnceWith(true);
  });
});

describe("workflow feedback", () => {
  it.each(["approve", "reject", "publish", "unpublish"])(
    "confirms article %s after the API succeeds",
    async (decision) => {
      vi.mocked(adminArticleReview).mockResolvedValueOnce(
        {} as Awaited<ReturnType<typeof adminArticleReview>>,
      );
      withAdmin(<ResourceWorkflow resource="articles" id="article-1" action="review" />);
      fireEvent.click(await screen.findByRole("combobox", { name: "Decision" }));
      fireEvent.click(screen.getByRole("option", { name: new RegExp(`^${decision}$`, "i") }));
      fireEvent.change(screen.getByLabelText(/Reason/), {
        target: { value: "Reviewed the article." },
      });
      fireEvent.click(screen.getByRole("button", { name: "Apply decision" }));
      const outcome = {
        approve: "approved",
        reject: "rejected",
        publish: "published",
        unpublish: "unpublished",
      }[decision];
      await waitFor(() => expect(toast.success).toHaveBeenCalledWith(`Article ${outcome}`));
      expect(router.replace).toHaveBeenCalledWith("/content/articles/article-1");
    },
  );
  it.each(["approved", "rejected"])("confirms source %s", async (decision) => {
    vi.mocked(getRecord).mockResolvedValueOnce({ id: "source-1", name: "News" });
    withAdmin(<ResourceWorkflow resource="sources" id="source-1" action="review" />);
    fireEvent.click(await screen.findByRole("combobox", { name: "Decision" }));
    fireEvent.click(screen.getByRole("option", { name: new RegExp(`^${decision}$`, "i") }));
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Reviewed." } });
    fireEvent.click(screen.getByRole("button", { name: "Apply decision" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(`Source ${decision}`));
    expect(adminSourceReview).toHaveBeenCalled();
  });
  it("reports queued work as requested, not completed", async () => {
    vi.mocked(adminSourceFetch).mockResolvedValueOnce({ id: "job-1" } as Awaited<
      ReturnType<typeof adminSourceFetch>
    >);
    withAdmin(<ResourceWorkflow resource="sources" id="source-1" action="fetch" />);
    fireEvent.click(await screen.findByRole("button", { name: "Queue fetch" }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Feed fetch requested", expect.any(Object)),
    );
    expect(router.replace).toHaveBeenCalledWith("/jobs/ingestion/job-1");
  });
  it("retains failed decisions and validation without a success or redirect", async () => {
    vi.mocked(adminArticleReview).mockRejectedValueOnce(
      new ApiError(422, "Please correct the highlighted fields.", { note: "Reason is required." }),
    );
    withAdmin(<ResourceWorkflow resource="articles" id="article-1" action="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "Apply decision" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Could not apply decision", expect.any(Object)),
    );
    expect(screen.getByRole("alert").textContent).toContain("Reason is required.");
    expect(toast.success).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  });
  it("confirms classification and explains its review reset", async () => {
    withAdmin(<ResourceWorkflow resource="articles" id="article-1" action="classify" />);
    fireEvent.click(await screen.findByRole("button", { name: "Save classification" }));
    await waitFor(() => expect(adminArticleClassify).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith(
      "Classification saved",
      expect.objectContaining({ description: expect.stringContaining("Approval has been reset") }),
    );
  });
  it.each([
    ["relevant", "relevant"],
    ["unrelated", "unrelated"],
    ["uncertain", "uncertain"],
    [undefined, "uncertain"],
    [null, "uncertain"],
    ["unknown", "uncertain"],
    [123, "uncertain"],
  ])(
    "preserves saved developer relevance %s while editing another classification field",
    async (saved, expected) => {
      vi.mocked(getRecord).mockResolvedValueOnce({
        ...article,
        classification_provenance: { origin: "manual", developer_relevance: saved },
      });
      withAdmin(<ResourceWorkflow resource="articles" id="article-1" action="classify" />);
      const selector = await screen.findByRole("combobox", { name: /Developer relevance/ });
      expect(selector.textContent?.toLowerCase()).toContain(expected);
      fireEvent.change(screen.getByLabelText("Review note"), {
        target: { value: "Keep the existing relevance decision." },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save classification" }));
      await waitFor(() =>
        expect(adminArticleClassify).toHaveBeenCalledWith(
          "article-1",
          expect.objectContaining({
            developer_relevance: expected,
            note: "Keep the existing relevance decision.",
            expected_revision: 1,
          }),
          expect.any(Object),
        ),
      );
    },
  );
  it("allows an administrator to explicitly change the saved relevance", async () => {
    vi.mocked(getRecord).mockResolvedValueOnce({
      ...article,
      classification_provenance: { developer_relevance: "relevant" },
    });
    withAdmin(<ResourceWorkflow resource="articles" id="article-1" action="classify" />);
    fireEvent.click(await screen.findByRole("combobox", { name: /Developer relevance/ }));
    fireEvent.click(screen.getByRole("option", { name: /^Unrelated$/i }));
    fireEvent.click(screen.getByRole("button", { name: "Save classification" }));
    await waitFor(() =>
      expect(adminArticleClassify).toHaveBeenCalledWith(
        "article-1",
        expect.objectContaining({ developer_relevance: "unrelated" }),
        expect.any(Object),
      ),
    );
  });
  it("reports an explicit overview retry, without toasting initial reads", async () => {
    vi.mocked(adminOverview)
      .mockRejectedValueOnce(new ApiError(503))
      .mockResolvedValueOnce({ ...overview, days: 7 });
    render(<Overview initialData={overview} />);
    expect(toast.success).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Overview refreshed"));
  });
});
