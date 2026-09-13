// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ReadLater } from "@/components/read-later";
import { userRequest } from "@/lib/user";
import { bookmarkChanged } from "@/components/article-engagement";
import { article } from "./fixtures";
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: { user_id: "owner" } }),
  AccountGate: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/loading-reveal", () => ({
  LoadingReveal: ({
    children,
    loading,
    fallback,
  }: {
    children: ReactNode;
    loading: boolean;
    fallback: ReactNode;
  }) => (loading ? fallback : children),
}));
vi.mock("@/components/infinite-feed", () => ({
  InfiniteFeed: ({
    initialPage,
    excludedIds,
  }: {
    initialPage: { items: { id: string; title: string }[] };
    excludedIds: string[];
  }) => (
    <div>
      {initialPage.items
        .filter((a) => !excludedIds.includes(a.id))
        .map((a) => (
          <p key={a.id}>{a.title}</p>
        ))}
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("retries a failed saved list and removes only this account's unsaved articles", async () => {
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("Offline"));
  render(<ReadLater />);
  await screen.findByRole("alert");
  vi.mocked(userRequest).mockResolvedValueOnce({ items: [article], next_cursor: null });
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText(article.title);
  fireEvent(
    window,
    new CustomEvent(bookmarkChanged, {
      detail: { owner: "someone-else", article_id: article.id, bookmarked: false },
    }),
  );
  expect(screen.getByText(article.title)).toBeTruthy();
  fireEvent(
    window,
    new CustomEvent(bookmarkChanged, {
      detail: { owner: "owner", article_id: article.id, bookmarked: false },
    }),
  );
  await waitFor(() => expect(screen.queryByText(article.title)).toBeNull());
  expect(userRequest).toHaveBeenLastCalledWith("bookmarks?limit=24", expect.anything());
});
