// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { UserRecords, UserAnalysisAction } from "@/components/organisms/user-details";
import { getRecord, listRecords, listUserRecords } from "@/lib/resource-api";
import { adminRouteTitle } from "@/lib/page-titles";
import { graphNodeHref } from "@/lib/knowledge-graph";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { adminUserAnalysis } from "@/lib/api/generated/admin";
import { notifyFailure } from "@/lib/notifications";
vi.mock("@/lib/api/generated/admin", async original => ({ ...await original<typeof import("@/lib/api/generated/admin")>(), adminUserAnalysis: vi.fn() }));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams("limit=25&offset=0"), usePathname: () => "/users" }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), getRecord: vi.fn(), listRecords: vi.fn(), listUserRecords: vi.fn() }));
const user: AdminUserDetail = { id: "user-1", name: "Ada", sign_in_name: "Ada Lovelace", email: "ada@example.test", avatar_url: null, created_at: "2026-09-11T10:00:00Z", last_seen_at: "2026-09-11T11:00:00Z", followed_topics: 1, liked_articles: 2, interests: 3, recommendations: 12, feed_status: "ready", computed_at: "2026-09-11T11:00:00Z", next_refresh_at: "2026-09-11T17:00:00Z", expires_at: "2026-09-12T11:00:00Z", refresh_attempts: 0 };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getRecord).mockResolvedValue({ ...user });
  vi.mocked(listRecords).mockResolvedValue({ items: [{ ...user }], total: 1, limit: 25, offset: 0 });
  vi.mocked(listUserRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
});
afterEach(cleanup);
it("uses the standard user table without add, edit or delete actions", async () => {
  renderAdmin(<ResourceList resource="users" />);
  expect((await screen.findByRole("link", { name: "Ada" })).getAttribute("href")).toBe("/users/user-1");
  expect(screen.getByText("ada@example.test")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /add user|edit|delete/i })).toBeNull();
  expect(listRecords).toHaveBeenCalledWith("users", { limit: 25, offset: 0, sort: "-created_at" }, expect.any(AbortSignal));
});
it("shows account information and links to existing-style detail sections", async () => {
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  expect(await screen.findByRole("heading", { name: "Ada" })).toBeTruthy();
  expect(screen.getByText("Ada Lovelace")).toBeTruthy();
  expect(screen.getByText("Recommendation refresh")).toBeTruthy();
  for (const section of ["Topics", "Sources", "Likes", "Interests", "Recommendations"]) expect(screen.getByRole("link", { name: section }).getAttribute("href")).toBe(`/users/user-1/${section.toLowerCase()}`);
  expect(screen.queryByText("Run details")).toBeNull();
  expect(screen.queryByRole("link", { name: "Logs" })).toBeNull();
  expect(screen.getByRole("link", { name: "Open in graph" }).getAttribute("href")).toBe("/knowledge/graph?focus=user%3Auser-1&layers=user");
});
it("labels stale prepared results and fetches only the selected user section", async () => {
  renderAdmin(<UserRecords user={{ ...user, feed_status: "refreshing" }} section="recommendations" />);
  await waitFor(() => expect(listUserRecords).toHaveBeenCalledWith("user-1", "recommendations", { limit: 25, offset: 0, sort: "position" }, expect.any(AbortSignal)));
  expect(screen.getByText(/not being served/)).toBeTruthy();
  expect(await screen.findByText("No prepared recommendations match these filters.")).toBeTruthy();
});
it("uses user titles and links graph nodes back to user details", () => {
  expect(adminRouteTitle({ view: "detail", resource: "users", id: "user-1", section: "details" }, "Ada")).toBe("Ada · User");
  expect(graphNodeHref({ id: "user:user-1", entity_id: "user-1", kind: "user", label: "Ada", description: null, status: null, subtype: null })).toBe("/users/user-1");
});
it("queues user analysis with CSRF and refreshes the displayed user", async () => {
  vi.mocked(adminUserAnalysis).mockResolvedValue({ ...user, feed_status: "refreshing" });
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  const button = await screen.findByRole("button", { name: "Rerun analysis" });
  await act(async () => { fireEvent.click(button); });
  expect(adminUserAnalysis).toHaveBeenCalledWith("user-1", { headers: { "X-CSRF-Token": "test-csrf" } });
  await waitFor(() => expect(getRecord).toHaveBeenCalledTimes(2));
});
it("blocks duplicate clicks while queuing and permits retry after failure", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(adminUserAnalysis).mockReturnValue(new Promise((_, failure) => { reject = failure; }));
  const refreshed = vi.fn();renderAdmin(<UserAnalysisAction id="user-1" onQueued={refreshed} />);
  fireEvent.click(screen.getByRole("button", { name: "Rerun analysis" }));
  const pending = screen.getByRole("button", { name: "Queuing analysis…" });
  expect((pending as HTMLButtonElement).disabled).toBe(true);fireEvent.click(pending);expect(adminUserAnalysis).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error("offline")));
  expect(refreshed).not.toHaveBeenCalled();expect(notifyFailure).toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Rerun analysis" }) as HTMLButtonElement).disabled).toBe(false);
});
