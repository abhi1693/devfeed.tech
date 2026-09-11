// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { FeedSettings } from "@/components/feed-settings";
import { ArticleGrid } from "@/components/article-grid";
import type { UserIdentity } from "@/lib/user";
import type { ReactNode } from "react";
import { article } from "./fixtures";
const session = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("@/components/user-account", () => ({ useUser: () => session, AccountGate: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: ReactNode }) => children,
  ArticleEngagement: () => <button>Like article</button>,
}));
const account = { user_id: "first", csrf_token: "csrf", name: "User", email: null, expires_at: 9999999999 };
function App() { return <FeedPreferencesProvider><FeedSettings /><ArticleGrid articles={[article]} /><ArticleGrid articles={[{ ...article, id: "second", title: "Second batch" }]} priority={false} /></FeedPreferencesProvider>; }
beforeEach(() => { session.user = null; localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("uses cards for anonymous users without fetching private settings", async () => {
  const fetcher = vi.fn();vi.stubGlobal("fetch", fetcher);
  render(<FeedPreferencesProvider><ArticleGrid articles={[article]} /></FeedPreferencesProvider>);
  expect(screen.queryByRole("table")).toBeNull();expect(screen.getByText(article.title)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});
it("loads account preferences and saves a layout only after Save changes", async () => {
  session.user = account;
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ view: "cards" })).mockResolvedValueOnce(Response.json({ view: "compact" }));vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("radio", { name: "Compact list" });
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Compact list" }));
  expect(screen.queryByRole("table")).toBeNull();expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/user/settings/feed");
  expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "PUT", body: '{"view":"compact"}', headers: { "X-CSRF-Token": "csrf" } });
});
it("preserves the selected view and allows retry after a failed save", async () => {
  session.user = account;const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ view: "compact" })).mockResolvedValueOnce(Response.json({}, { status: 503 })).mockResolvedValueOnce(Response.json({ view: "cards" }));vi.stubGlobal("fetch", fetcher);
  render(<App />);await screen.findAllByRole("table");
  fireEvent.click(screen.getByRole("radio", { name: "Cards" }));fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toBeTruthy();expect(screen.getAllByRole("table")).toHaveLength(2);
  fireEvent.click(screen.getByRole("radio", { name: "Cards" }));fireEvent.click(screen.getByRole("button", { name: "Save changes" }));await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
});
it("does not show the previous account's view while another account loads", async () => {
  session.user = account;vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ view: "compact" })).mockImplementation(() => new Promise(() => {})));
  const app=render(<App />);await screen.findAllByRole("table");session.user={ ...account, user_id:"second" };app.rerender(<App />);
  expect(screen.queryByRole("table")).toBeNull();expect(screen.getByText("Loading feed settings…")).toBeTruthy();
});
it("lets users retry an unavailable account preference without overwriting it", async () => {
  session.user = account;const fetcher = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(Response.json({ view: "compact" }));vi.stubGlobal("fetch", fetcher);
  render(<App />);fireEvent.click(await screen.findByRole("button",{name:"Retry"}));await screen.findAllByRole("table");expect(fetcher).toHaveBeenCalledTimes(2);
});
