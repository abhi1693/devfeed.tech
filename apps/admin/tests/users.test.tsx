// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import {
  UserRecords,
  UserAnalysis,
  UserAnalysisAction,
  UserDetailsOverview,
} from "@/components/organisms/user-details";
import { getRecord, listRecords, listUserRecords } from "@/lib/resource-api";
import { adminRouteTitle } from "@/lib/page-titles";
import type { AdminUserDetail } from "@/lib/api/generated/models";
import { adminUserAnalysis, adminUserMustReads } from "@/lib/api/generated/admin";
import { notifyFailure } from "@/lib/notifications";
vi.mock("@/lib/api/generated/admin", async (original) => ({
  ...(await original<typeof import("@/lib/api/generated/admin")>()),
  adminUserAnalysis: vi.fn(),
  adminUserMustReads: vi.fn(),
}));
vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams("limit=25&offset=0"),
  usePathname: () => "/users",
}));
vi.mock("@/lib/resource-api", async (original) => ({
  ...(await original<typeof import("@/lib/resource-api")>()),
  getRecord: vi.fn(),
  listRecords: vi.fn(),
  listUserRecords: vi.fn(),
}));
const user: AdminUserDetail = {
  id: "user-1",
  name: "Ada",
  sign_in_name: "Ada Lovelace",
  email: "ada@example.test",
  avatar_url: null,
  created_at: "2026-09-11T10:00:00Z",
  last_seen_at: "2026-09-11T11:00:00Z",
  username: "ada",
  followed_topics: 1,
  followed_sources: 0,
  liked_articles: 2,
  bookmarks: 1,
  reads: 2,
  reading_days: 1,
  last_read_at: "2026-09-11T11:00:00Z",
  profile_bio: "Writes about computing",
  profile_location: "London",
  profile_about: "Analytical engines",
  profile_public: true,
  profile_links: [{ url: "https://example.test/ada", label: "Website" }],
  stack: [{ id: "topic-1", name: "Python", section: "primary", since_year: 2020 }],
  dev_card: {
    theme: "terminal",
    accent: "teal",
    motion: "static",
    technologies: ["topic-1"],
    stats: ["current_streak"],
  },
  dev_card_technologies: [{ id: "topic-1", name: "Python" }],
  reading_streak: { current_days: 2, longest_days: 5, total_days: 8, last_read_date: "2026-09-11" },
  feed_preferences: { view: "cards", languages: ["en"], content_types: ["article"] },
  appearance_preferences: {
    theme: "system",
    timezone: "local",
    time_format: "system",
    date_format: "locale",
  },
  notification_preferences: { show_badge: true, sound: false },
  interests: 3,
  recommendations: 12,
  feed_status: "ready",
  computed_at: "2026-09-11T11:00:00Z",
  next_refresh_at: "2026-09-11T17:00:00Z",
  expires_at: "2026-09-12T11:00:00Z",
  refresh_attempts: 0,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(adminUserMustReads).mockResolvedValue({
    selection_date: "2026-10-04",
    timezone: "Asia/Kolkata",
    generated: false,
    presented_at: null,
    items: [],
  });
  vi.mocked(getRecord).mockResolvedValue({ ...user });
  vi.mocked(listRecords).mockResolvedValue({
    items: [{ ...user }],
    total: 1,
    limit: 25,
    offset: 0,
  });
  vi.mocked(listUserRecords).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
});
afterEach(cleanup);
it("uses the standard user table without add, edit or delete actions", async () => {
  renderAdmin(<ResourceList resource="users" />);
  expect((await screen.findByRole("link", { name: "Ada" })).getAttribute("href")).toBe(
    "/users/user-1",
  );
  expect(screen.getByText("ada@example.test")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /add user|edit|delete/i })).toBeNull();
  expect(listRecords).toHaveBeenCalledWith(
    "users",
    { limit: 25, offset: 0, sort: "-created_at" },
    expect.any(AbortSignal),
  );
});
it("shows account information and links to existing-style detail sections", async () => {
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  expect(await screen.findByRole("heading", { name: "Ada" })).toBeTruthy();
  expect(screen.getByText("Ada Lovelace")).toBeTruthy();
  expect(screen.getByText("Recommendation refresh")).toBeTruthy();
  expect(screen.getByText("Dev Card")).toBeTruthy();
  expect(screen.getByText("Terminal")).toBeTruthy();
  expect(screen.getByText("London")).toBeTruthy();
  for (const section of [
    "Analysis",
    "Topics",
    "Sources",
    "Likes",
    "Bookmarks",
    "Reads",
    "Interests",
    "Recommendations",
  ])
    expect(screen.getByRole("link", { name: section }).getAttribute("href")).toBe(
      `/users/user-1/${section.toLowerCase()}`,
    );
  expect(screen.getByRole("link", { name: "Reading days" }).getAttribute("href")).toBe(
    "/users/user-1/reading-days",
  );
  expect(screen.queryByText("Run details")).toBeNull();
  expect(screen.queryByRole("link", { name: "Logs" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Open in graph" })).toBeNull();
});

it.each([null, undefined, "", "   "])(
  "omits profile and Dev Card details when the username is %j",
  async (username) => {
    vi.mocked(getRecord).mockResolvedValue({ ...user, username });
    renderAdmin(<ResourceDetail resource="users" id="user-1" />);
    await screen.findByText("Account");
    expect(screen.queryByText("Reader profile")).toBeNull();
    expect(screen.queryByText("Dev Card styling")).toBeNull();
    expect(screen.queryByText("Dev Card")).toBeNull();
    expect(screen.queryByRole("link", { name: "Website" })).toBeNull();
    expect(screen.queryByText("London")).toBeNull();
    for (const title of [
      "Reading activity",
      "Recommendation refresh",
      "Personalization",
      "Reader preferences",
      "Technical identifiers",
    ]) {
      expect(screen.getByText(title)).toBeTruthy();
    }
    expect(screen.getByText("Longest streak")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Reading days" })).toBeTruthy();
  },
);

it("shows claimed profile details for private profiles and updates when the username changes", () => {
  const view = render(<UserDetailsOverview user={{ ...user, profile_public: false }} />);
  expect(screen.getByText("Reader profile")).toBeTruthy();
  expect(screen.getByText("Hidden")).toBeTruthy();
  expect(screen.getByText("Dev Card styling")).toBeTruthy();
  view.rerender(<UserDetailsOverview user={{ ...user, username: null }} />);
  expect(screen.queryByText("Reader profile")).toBeNull();
  expect(screen.queryByText("Dev Card styling")).toBeNull();
  view.rerender(<UserDetailsOverview user={user} />);
  expect(screen.getByText("Reader profile")).toBeTruthy();
  expect(screen.getByText("Dev Card styling")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Website" })).toBeTruthy();
});

it("labels stale prepared results and fetches only the selected user section", async () => {
  renderAdmin(
    <UserRecords user={{ ...user, feed_status: "refreshing" }} section="recommendations" />,
  );
  await waitFor(() =>
    expect(listUserRecords).toHaveBeenCalledWith(
      "user-1",
      "recommendations",
      { limit: 25, offset: 0, sort: "position" },
      expect.any(AbortSignal),
    ),
  );
  expect(screen.getByText(/not being served/)).toBeTruthy();
  expect(await screen.findByText("No prepared recommendations match these filters.")).toBeTruthy();
});
it("loads saved articles and original article clicks as separate user records", async () => {
  const view = renderAdmin(<UserRecords user={user} section="bookmarks" />);
  await waitFor(() =>
    expect(listUserRecords).toHaveBeenCalledWith(
      "user-1",
      "bookmarks",
      { limit: 25, offset: 0, sort: "-bookmarked_at" },
      expect.any(AbortSignal),
    ),
  );
  view.rerender(<UserRecords user={user} section="reads" />);
  await waitFor(() =>
    expect(listUserRecords).toHaveBeenCalledWith(
      "user-1",
      "reads",
      { limit: 25, offset: 0, sort: "-opened_at" },
      expect.any(AbortSignal),
    ),
  );
  view.rerender(<UserRecords user={user} section="reading-days" />);
  await waitFor(() =>
    expect(listUserRecords).toHaveBeenCalledWith(
      "user-1",
      "reading-days",
      { limit: 25, offset: 0, sort: "-read_date" },
      expect.any(AbortSignal),
    ),
  );
});
it("shows a reading day and keeps deleted articles in click history", async () => {
  vi.mocked(listUserRecords).mockResolvedValueOnce({
    items: [
      {
        id: "2026-09-11:article-1",
        article_id: "article-1",
        title: null,
        publication_status: null,
        opened_at: "2026-09-11T11:00:00Z",
        read_date: "2026-09-11",
      },
    ],
    total: 1,
    limit: 25,
    offset: 0,
  });
  const view = renderAdmin(<UserRecords user={user} section="reads" />);
  expect(await screen.findByText("Article no longer available")).toBeTruthy();
  view.unmount();
  vi.mocked(listUserRecords).mockResolvedValueOnce({
    items: [
      {
        id: "2026-09-11",
        read_date: "2026-09-11",
        article_count: 2,
        last_read_at: "2026-09-11T11:00:00Z",
      },
    ],
    total: 1,
    limit: 25,
    offset: 0,
  });
  renderAdmin(<UserRecords user={user} section="reading-days" />);
  expect(await screen.findByText("Articles clicked")).toBeTruthy();
  expect(await screen.findByText("2")).toBeTruthy();
});
it("uses user titles", () => {
  expect(
    adminRouteTitle({ view: "detail", resource: "users", id: "user-1", section: "details" }, "Ada"),
  ).toBe("Ada · User");
});
it("queues user analysis with CSRF and refreshes the displayed user", async () => {
  vi.mocked(adminUserAnalysis).mockResolvedValue({ ...user, feed_status: "refreshing" });
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  const button = await screen.findByRole("button", { name: "Rerun analysis" });
  await act(async () => {
    fireEvent.click(button);
  });
  expect(adminUserAnalysis).toHaveBeenCalledWith("user-1", {
    headers: { "X-CSRF-Token": "test-csrf" },
  });
  await waitFor(() => expect(getRecord).toHaveBeenCalledTimes(2));
});
it("blocks duplicate clicks while queuing and permits retry after failure", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(adminUserAnalysis).mockReturnValue(
    new Promise((_, failure) => {
      reject = failure;
    }),
  );
  const refreshed = vi.fn();
  renderAdmin(<UserAnalysisAction id="user-1" onQueued={refreshed} />);
  fireEvent.click(screen.getByRole("button", { name: "Rerun analysis" }));
  const pending = screen.getByRole("button", { name: "Queuing analysis…" });
  expect((pending as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(pending);
  expect(adminUserAnalysis).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error("offline")));
  expect(refreshed).not.toHaveBeenCalled();
  expect(notifyFailure).toHaveBeenCalled();
  expect(
    (screen.getByRole("button", { name: "Rerun analysis" }) as HTMLButtonElement).disabled,
  ).toBe(false);
});

it("replaces the old top-five preview with the user's saved daily Must Reads", async () => {
  vi.mocked(listUserRecords).mockResolvedValue({
    items: [
      {
        id: "topic-2",
        name: "FastAPI",
        status: "active",
        weight: 40,
        reason: "related_topic",
        seed_topic_id: "topic-1",
        seed_topic_name: "Python",
      },
    ],
    total: 1,
    limit: 5,
    offset: 0,
  });
  vi.mocked(adminUserMustReads).mockResolvedValue({
    selection_date: "2026-10-04",
    timezone: "Asia/Kolkata",
    generated: true,
    presented_at: null,
    items: [
      {
        id: "article-1",
        title: "Your saved daily pick",
        position: 1,
        reason: "Because you follow Python Weekly",
        read: true,
      },
    ],
  });
  renderAdmin(<ResourceDetail resource="users" id="user-1" section="analysis" />);
  expect(await screen.findByRole("heading", { name: "User analysis" })).toBeTruthy();
  const interests = within(screen.getByRole("region", { name: "Strongest topic interests" }));
  expect((await interests.findByRole("link", { name: "FastAPI" })).getAttribute("href")).toBe(
    "/taxonomy/topics/topic-2",
  );
  expect(interests.getByText("Related topic")).toBeTruthy();
  const picks = within(screen.getByRole("region", { name: "Today’s Must Reads" }));
  expect(
    (await picks.findByRole("link", { name: "Your saved daily pick" })).getAttribute("href"),
  ).toBe("/content/articles/article-1");
  expect(picks.getByText("Because you follow Python Weekly")).toBeTruthy();
  expect(picks.getByText("1 of 1 read")).toBeTruthy();
  expect(picks.getByText("2026-10-04 · Asia/Kolkata")).toBeTruthy();
  expect(picks.getByText("Not shown yet")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Top recommendations" })).toBeNull();
  expect(picks.queryByText("Score")).toBeNull();
  expect(adminUserMustReads).toHaveBeenCalledWith("user-1", undefined, {
    signal: expect.any(AbortSignal),
  });
  expect(listUserRecords).toHaveBeenCalledTimes(1);
  expect(listUserRecords).toHaveBeenCalledWith(
    "user-1",
    "interests",
    { limit: 5, offset: 0, sort: "-weight" },
    expect.any(AbortSignal),
  );
});

it("shows an honest empty state and retries failed Must Reads independently", async () => {
  vi.mocked(adminUserMustReads).mockRejectedValueOnce(new Error("Offline"));
  renderAdmin(<UserAnalysis user={user} />);
  const picks = within(screen.getByRole("region", { name: "Today’s Must Reads" }));
  fireEvent.click(await picks.findByRole("button", { name: "Retry" }));
  expect(
    await picks.findByText("No Must Reads have been generated for this user today."),
  ).toBeTruthy();
  expect(adminUserMustReads).toHaveBeenCalledTimes(2);
});

it("distinguishes uncomputed analysis from stale results and reloads on computation changes", async () => {
  const view = renderAdmin(
    <UserAnalysis
      user={{
        ...user,
        computed_at: null,
        feed_status: "pending",
        interests: 0,
        recommendations: 0,
      }}
    />,
  );
  expect(screen.getByText(/Analysis has not completed yet/)).toBeTruthy();
  expect(await screen.findByText(/No topic interests were stored/)).toBeTruthy();
  await waitFor(() => expect(listUserRecords).toHaveBeenCalledTimes(1));
  view.rerender(<UserAnalysis user={{ ...user, feed_status: "refreshing" }} />);
  expect(screen.getByText(/Previous analysis results/)).toBeTruthy();
  await waitFor(() => expect(listUserRecords).toHaveBeenCalledTimes(2));
  view.rerender(<UserAnalysis user={{ ...user, computed_at: "2026-09-12T12:00:00Z" }} />);
  expect(screen.getByText(/Latest stored interests/)).toBeTruthy();
  await waitFor(() => expect(listUserRecords).toHaveBeenCalledTimes(3));
});

it("retries a failed analysis preview independently", async () => {
  vi.mocked(listUserRecords).mockRejectedValueOnce(new Error("Offline"));
  renderAdmin(<UserAnalysis user={user} />);
  const interests = within(screen.getByRole("region", { name: "Strongest topic interests" }));
  fireEvent.click(await interests.findByRole("button", { name: "Retry" }));
  expect(await interests.findByText(/No topic interests were stored/)).toBeTruthy();
  expect(listUserRecords).toHaveBeenCalledTimes(2);
});

it("skips analysis when every personalization count is zero", async () => {
  vi.mocked(getRecord).mockResolvedValue({
    ...user,
    followed_topics: 0,
    followed_sources: 0,
    liked_articles: 0,
    interests: 0,
    recommendations: 0,
  });
  renderAdmin(<ResourceDetail resource="users" id="user-1" section="analysis" />);
  const button = await screen.findByRole("button", { name: "Rerun analysis" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(adminUserAnalysis).not.toHaveBeenCalled();
  expect(screen.getByText("Not needed")).toBeTruthy();
  expect(screen.getByText(/Analysis is skipped/)).toBeTruthy();
});

it.each([
  "followed_topics",
  "followed_sources",
  "liked_articles",
  "interests",
  "recommendations",
] as const)("allows analysis when only %s is nonzero", async (field) => {
  vi.mocked(getRecord).mockResolvedValue({
    ...user,
    followed_topics: 0,
    followed_sources: 0,
    liked_articles: 0,
    interests: 0,
    recommendations: 0,
    [field]: 1,
  });
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  expect(
    ((await screen.findByRole("button", { name: "Rerun analysis" })) as HTMLButtonElement).disabled,
  ).toBe(false);
});

it("groups essential user details and collapses secondary settings", async () => {
  renderAdmin(<ResourceDetail resource="users" id="user-1" />);
  await screen.findByText("Account");
  expect(screen.getByText("Reading activity")).toBeTruthy();
  for (const title of ["Dev Card styling", "Reader preferences", "Technical identifiers"]) {
    const summary = screen.getByText(title);
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
  }
  expect(screen.getByRole("link", { name: "Website" }).getAttribute("href")).toBe(
    "https://example.test/ada",
  );
  expect(screen.getByRole("link", { name: "Python" }).getAttribute("href")).toBe(
    "/taxonomy/topics/topic-1",
  );
  expect(screen.queryByText("Since year")).toBeNull();
  expect(screen.getAllByText("Last active")).toHaveLength(1);
});

it("shows partner account links under Account only for memberships", () => {
  const view = render(<UserDetailsOverview user={user} />);
  expect(screen.queryByText("Partner accounts")).toBeNull();
  view.rerender(
    <UserDetailsOverview
      user={{
        ...user,
        partner_accounts: [
          { id: "alpha", name: "Alpha partnership" },
          { id: "beta", name: "Beta partnership" },
        ],
      }}
    />,
  );
  const accountPanel = screen
    .getByText("Account", { exact: true })
    .closest<HTMLElement>('[data-slot="card"]')!;
  expect(
    within(accountPanel).getByRole("link", { name: "Alpha partnership" }).getAttribute("href"),
  ).toBe("/partnerships/accounts/alpha");
  expect(
    within(accountPanel).getByRole("link", { name: "Beta partnership" }).getAttribute("href"),
  ).toBe("/partnerships/accounts/beta");
});
