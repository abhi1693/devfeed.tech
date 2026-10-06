// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { ArticleTable } from "@/components/article-table";
import { EngagementProvider } from "@/components/article-engagement";
import { userRequest, type UserIdentity } from "@/lib/user";
import { renderToStaticMarkup } from "react-dom/server";
import type { Article } from "@/lib/types";
import { article, topic, source } from "./fixtures";

const account = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  profile: null,
  refreshProfile: vi.fn(),
}));
const preferences = vi.hoisted(() => ({ view: "compact" as "cards" | "compact", loading: false }));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));

const engagement = { opens: 9, likes: 5, liked: false, bookmarked: false };
const signedIn: UserIdentity = {
  user_id: "reader",
  csrf_token: "csrf",
  name: "Reader",
  email: null,
  expires_at: 4102444800,
};

function Table({
  articles = [article],
  showHeader = true,
}: {
  articles?: Article[];
  showHeader?: boolean;
}) {
  return (
    <EngagementProvider articleIds={articles.map((item) => item.id)}>
      <ArticleTable articles={articles} showHeader={showHeader} />
    </EngagementProvider>
  );
}

function row() {
  const table = screen.getByRole("table", { name: "Articles in compact view" });
  return within(within(table).getAllByRole("row")[1]);
}

beforeEach(() => {
  account.user = null;
  account.loading = false;
  preferences.view = "compact";
  preferences.loading = false;
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path, init) => {
      if (path.startsWith("engagement?"))
        return new URLSearchParams(path.split("?")[1]).getAll("article_id").map((id) => ({
          article_id: id,
          ...engagement,
        }));
      if (path.endsWith("/bookmark"))
        return {
          article_id: path.split("/")[1],
          bookmarked: JSON.parse(String(init?.body)).bookmarked,
        };
      throw new Error("Unexpected article request: " + path);
    });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it.each([true, false])(
  "keeps four essential accessible columns when showHeader is %s",
  (showHeader) => {
    render(<Table showHeader={showHeader} />);
    const headers = screen.getAllByRole("columnheader");
    expect(headers.map((header) => header.textContent)).toEqual([
      "Article",
      "Source",
      "Published",
      "Bookmark",
    ]);
    expect(headers.every((header) => header.getAttribute("scope") === "col")).toBe(true);
    expect(row().getAllByRole("cell")).toHaveLength(4);
  },
);

it("keeps public article links in server-rendered content while preferences load", () => {
  preferences.loading = true;
  preferences.view = "cards";
  const html = renderToStaticMarkup(<ArticleGrid articles={[article]} />);
  expect(html).toContain(`/articles/${article.slug}`);
  expect(html).toContain(article.title);
});

it("waits for saved layout before mounting private feed cards or table controls", async () => {
  preferences.loading = true;
  preferences.view = "cards";
  const view = render(<ArticleGrid articles={[article]} waitForPreferences />);
  expect(screen.getByRole("status", { name: "Loading articles…" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("link", { name: article.title })).toBeNull();
  expect(view.container.querySelector(".shimmer")).toBeNull();
  expect(userRequest).not.toHaveBeenCalled();
  preferences.loading = false;
  preferences.view = "compact";
  view.rerender(<ArticleGrid articles={[article]} waitForPreferences />);
  await screen.findByRole("table");
  expect(view.container.querySelector(".article-card")).toBeNull();
});

it("removes metadata and engagement clutter from list mode while retaining them on cards", async () => {
  const props = {
    articles: [article],
    reasons: { [article.id]: { kind: "followed_source" as const } },
  };
  preferences.view = "cards";
  const view = render(<ArticleGrid {...props} />);
  await screen.findByRole("img", { name: "9 clicks to the original article" });
  expect(screen.getByText(article.content_type)).toBeTruthy();
  expect(screen.getByRole("link", { name: `#${topic.name}` })).toBeTruthy();
  expect(screen.getByText("From a source you follow")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in to like this article, 5 likes" })).toBeTruthy();

  preferences.view = "compact";
  view.rerender(<ArticleGrid {...props} />);
  expect(row().getAllByRole("cell")[0].textContent).toBe(article.title);
  expect(screen.queryByText(article.content_type)).toBeNull();
  expect(screen.queryByRole("link", { name: `#${topic.name}` })).toBeNull();
  expect(screen.queryByText("From a source you follow")).toBeNull();
  expect(screen.queryByRole("img", { name: /clicks to the original article/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /like this article/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Sign in to save article for later" })).toBeTruthy();

  preferences.view = "cards";
  view.rerender(<ArticleGrid {...props} />);
  expect(screen.getByRole("img", { name: "9 clicks to the original article" })).toBeTruthy();
  expect(screen.getByRole("link", { name: `#${topic.name}` })).toBeTruthy();
  expect(screen.getByText("From a source you follow")).toBeTruthy();
});

it("retains the full Unicode article title, article route, and source route without duplicate row metadata", () => {
  const item = { ...article, title: article.title + " — 日本語 🦀 " + "a long title ".repeat(15) };
  render(<Table articles={[item]} />);
  const link = row().getByRole("link", { name: item.title.trim() });
  expect(link.getAttribute("href")).toBe(`/articles/${item.slug}`);
  expect(link.getAttribute("title")).toBe(item.title);
  expect(link.textContent).toBe(item.title);
  const cells = row().getAllByRole("cell");
  expect(within(cells[0]).getAllByRole("link")).toHaveLength(1);
  const publisher = within(cells[1]).getByRole("link", { name: source.name });
  expect(publisher.getAttribute("href")).toBe(`/sources/${source.slug}`);
  expect(publisher.getAttribute("title")).toBe(source.name);
  expect(cells[0].querySelector("time")).toBeNull();
});

it.each([article.published_at, null])(
  "uses the correct single publication date when published_at is %s",
  (published_at) => {
    const item = { ...article, published_at, feed_at: "2026-09-22T03:04:05Z" };
    render(<Table articles={[item]} />);
    const dateCell = row().getAllByRole("cell")[2];
    expect(dateCell.querySelectorAll("time")).toHaveLength(1);
    expect(dateCell.querySelector("time")?.getAttribute("datetime")).toBe(
      published_at ?? item.feed_at,
    );
    expect(dateCell.querySelector("time")?.getAttribute("title")).toBeTruthy();
  },
);

it.each([
  ["https://www.publisher.example/story", "publisher.example"],
  ["invalid publisher URL", "Original publisher"],
])("keeps a useful source fallback for %s", (canonical_url, label) => {
  render(<Table articles={[{ ...article, sources: [], canonical_url }]} />);
  const cell = row().getAllByRole("cell")[1];
  expect(within(cell).getByText(label).getAttribute("title")).toBe(label);
  expect(within(cell).queryByRole("link")).toBeNull();
});

it("keeps anonymous bookmarks as an explicit sign-in link back to the article", async () => {
  render(<Table />);
  await act(async () => {});
  const save = row().getByRole("link", { name: "Sign in to save article for later" });
  const destination = new URL(save.getAttribute("href")!, window.location.origin);
  expect(destination.pathname).toBe("/login");
  expect(destination.searchParams.get("return_to")).toBe(`/articles/${article.slug}`);
  expect(vi.mocked(userRequest).mock.calls.every(([, init]) => init?.method !== "PUT")).toBe(true);
});

it("saves and removes bookmarks with CSRF while keeping the list free of like and view controls", async () => {
  account.user = signedIn;
  render(<Table />);
  const save = screen.getByRole("button", { name: "Save article for later" });
  await waitFor(() => expect(save).toHaveProperty("disabled", false));
  save.focus();
  fireEvent.click(save);
  const remove = await screen.findByRole("button", { name: "Remove bookmark" });
  expect(remove.getAttribute("aria-pressed")).toBe("true");
  expect(document.activeElement).toBe(save);
  expect(userRequest).toHaveBeenLastCalledWith(
    `articles/${article.id}/bookmark`,
    expect.objectContaining({
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: JSON.stringify({ bookmarked: true }),
    }),
  );
  expect(screen.queryByRole("button", { name: /Like article/ })).toBeNull();
  expect(screen.queryByRole("img", { name: /clicks to the original article/ })).toBeNull();
  fireEvent.click(remove);
  await screen.findByRole("button", { name: "Save article for later" });
  expect(userRequest).toHaveBeenLastCalledWith(
    `articles/${article.id}/bookmark`,
    expect.objectContaining({ body: JSON.stringify({ bookmarked: false }) }),
  );
});

it("shows a failed bookmark write and permits recovery without pretending the article was saved", async () => {
  account.user = signedIn;
  render(<Table />);
  const save = screen.getByRole("button", { name: "Save article for later" });
  await waitFor(() => expect(save).toHaveProperty("disabled", false));
  vi.mocked(userRequest).mockRejectedValueOnce(new Error("Offline"));
  fireEvent.click(save);
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn’t update bookmark");
  expect(screen.queryByRole("button", { name: "Remove bookmark" })).toBeNull();
  fireEvent.click(save);
  await screen.findByRole("button", { name: "Remove bookmark" });
});

it("waits for session resolution before exposing the anonymous save link", async () => {
  account.loading = true;
  const view = render(<Table />);
  expect(screen.getByRole("button", { name: "Save article for later" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.queryByRole("link", { name: "Sign in to save article for later" })).toBeNull();
  expect(userRequest).not.toHaveBeenCalled();
  account.loading = false;
  view.rerender(<Table />);
  await screen.findByRole("link", { name: "Sign in to save article for later" });
});
