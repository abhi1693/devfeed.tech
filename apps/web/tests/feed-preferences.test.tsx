// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { contentTypes } from "@/lib/feed-query";
import { FeedSettings } from "@/components/feed-settings";
import { ArticleGrid } from "@/components/article-grid";
import { FeedViewToggle } from "@/components/feed-view-toggle";
import type { UserIdentity } from "@/lib/user";
import type { ReactNode } from "react";
import { article } from "./fixtures";
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const session = vi.hoisted(() => ({ user: null as UserIdentity | null, loading: false }));
vi.mock("@/components/user-account", () => ({
  useUser: () => session,
  AccountGate: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/article-engagement", () => ({
  ArticleBookmarkButton: () => null,
  ArticleReadLink: ({ children }: { children: ReactNode }) => children,
  EngagementProvider: ({ children }: { children: ReactNode }) => children,
  ArticleEngagement: () => <button>Like article</button>,
  useArticleEngagement: () => undefined,
}));
const account = {
  user_id: "first",
  csrf_token: "csrf",
  name: "User",
  email: null,
  expires_at: 9999999999,
};
function App() {
  return (
    <FeedPreferencesProvider>
      <FeedSettings />
      <ArticleGrid articles={[article]} />
      <ArticleGrid
        articles={[{ ...article, id: "second", title: "Second batch" }]}
        priority={false}
      />
    </FeedPreferencesProvider>
  );
}
function InlineApp() {
  return (
    <FeedPreferencesProvider>
      <FeedViewToggle />
      <ArticleGrid articles={[article]} />
      <ArticleGrid
        articles={[{ ...article, id: "second", title: "Second batch" }]}
        priority={false}
      />
    </FeedPreferencesProvider>
  );
}
beforeEach(() => {
  session.user = null;
  localStorage.clear();
  session.loading = false;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("switches every loaded batch for guests and restores the choice after remounting", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const app = render(<InlineApp />);
  const grid = screen.getByRole("button", { name: "Grid view" });
  await waitFor(() => expect(grid.hasAttribute("disabled")).toBe(false));
  expect(grid.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "List view" }));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(screen.getByText("Second batch")).toBeTruthy();
  app.unmount();
  render(<InlineApp />);
  await screen.findAllByRole("table");
  fireEvent.click(screen.getByRole("button", { name: "Grid view" }));
  expect(screen.queryByRole("table")).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it("keeps layouts usable when browser storage is restricted", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  render(<InlineApp />);
  const list = screen.getByRole("button", { name: "List view" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  expect(screen.getAllByRole("table")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Grid view" }));
  expect(screen.queryByRole("table")).toBeNull();
});

it("keeps guest layout choices separate from account settings and ignores invalid values", async () => {
  localStorage.setItem("devfeed:feed-view:guest", "unknown");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ view: "cards" }));
  vi.stubGlobal("fetch", fetcher);
  const app = render(<InlineApp />);
  const list = screen.getByRole("button", { name: "List view" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(list);
  session.user = account;
  app.rerender(<InlineApp />);
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  expect(fetcher).toHaveBeenCalledTimes(1);
  session.user = null;
  app.rerender(<InlineApp />);
  await screen.findAllByRole("table");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("switches layout immediately, saves existing filters and coalesces repeated clicks", async () => {
  session.user = account;
  const settings = { view: "cards", content_types: ["news"], languages: ["fr", "en"] };
  let finish!: (response: Response) => void;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(settings))
    .mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<InlineApp />);
  const list = screen.getByRole("button", { name: "List view" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  fireEvent.click(list);
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(list.hasAttribute("disabled")).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    method: "PUT",
    headers: { "X-CSRF-Token": "csrf" },
    body: JSON.stringify({ ...settings, view: "compact" }),
  });
  await act(async () => finish(Response.json({ ...settings, view: "compact" })));
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
});

it("rolls back a rejected inline save, announces it and lets the reader retry", async () => {
  session.user = account;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ view: "cards" }))
      .mockResolvedValueOnce(Response.json({}, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ view: "compact" })),
  );
  render(<InlineApp />);
  const list = screen.getByRole("button", { name: "List view" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  await screen.findByRole("alert");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("button", { name: "Grid view" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  fireEvent.click(list);
  await screen.findAllByRole("table");
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

it("ignores a previous account's inline save after another account restores its settings", async () => {
  session.user = account;
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ view: "cards" }))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValueOnce(Response.json({ view: "cards" })),
  );
  const app = render(<InlineApp />);
  const list = screen.getByRole("button", { name: "List view" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  session.user = { ...account, user_id: "second" };
  app.rerender(<InlineApp />);
  await act(async () => {});
  await act(async () => finish(Response.json({ view: "compact" })));
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("button", { name: "Grid view" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
});

it("retries unavailable preferences before allowing an inline write", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(Response.json({ view: "compact" }));
  vi.stubGlobal("fetch", fetcher);
  render(<InlineApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry feed settings" }));
  await screen.findAllByRole("table");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.every(([, init]) => !init.method)).toBe(true);
});

it("uses cards for anonymous users without fetching private settings", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ArticleGrid articles={[article]} />
    </FeedPreferencesProvider>,
  );
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});
it("loads account preferences and saves a layout only after Save changes", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "cards" }))
    .mockResolvedValueOnce(Response.json({ view: "compact" }));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("radio", { name: "Compact list" });
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Compact list" }));
  expect(screen.queryByRole("table")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/user/settings/feed");
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    method: "PUT",
    body: JSON.stringify({ view: "compact", content_types: [...contentTypes], languages: ["en"] }),
    headers: { "X-CSRF-Token": "csrf" },
  });
});
it("preserves the selected view and allows retry after a failed save", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "compact" }))
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ view: "cards" }));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findAllByRole("table");
  fireEvent.click(screen.getByRole("radio", { name: "Cards" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getAllByRole("table")).toHaveLength(2);
  fireEvent.click(screen.getByRole("radio", { name: "Cards" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
});
it("does not show the previous account's view while another account loads", async () => {
  session.user = account;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ view: "compact" }))
      .mockImplementation(() => new Promise(() => {})),
  );
  const app = render(<App />);
  await screen.findAllByRole("table");
  session.user = { ...account, user_id: "second" };
  app.rerender(<App />);
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText("Loading feed settings…")).toBeTruthy();
});
it("lets users retry an unavailable account preference without overwriting it", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(Response.json({ view: "compact" }));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await screen.findAllByRole("table");
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it("saves content selections with layout, prevents empty saves and resets all types", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "compact", content_types: ["news", "tutorial"] }))
    .mockResolvedValueOnce(
      Response.json({ view: "compact", content_types: ["article", "news", "tutorial"] }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const news = await screen.findByRole("checkbox", { name: "News" });
  expect((news as HTMLInputElement).checked).toBe(true);
  const articles = screen.getByRole("checkbox", { name: "Articles" });
  expect((articles as HTMLInputElement).checked).toBe(false);
  fireEvent.click(articles);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    view: "compact",
    languages: ["en"],
    content_types: ["article", "news", "tutorial"],
  });
  for (const name of ["Articles", "News", "Tutorials"])
    fireEvent.click(screen.getByRole("checkbox", { name }));
  expect(screen.getByRole("alert").textContent).toBe("Select at least one content type.");
  expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
  for (const name of ["Articles", "News", "Tutorials", "Releases", "Comparisons", "Opinions"])
    expect((screen.getByRole("checkbox", { name }) as HTMLInputElement).checked).toBe(true);
});

it("defaults to English, saves multiple languages and prevents an empty selection", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "cards", content_types: [...contentTypes] }))
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const english = await screen.findByRole("checkbox", { name: "English" });
  expect((english as HTMLInputElement).checked).toBe(true);
  fireEvent.click(english);
  expect(screen.getByRole("alert").textContent).toBe("Select at least one language.");
  expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.click(english);
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a language" }), {
    target: { value: "French" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "French" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(JSON.parse(fetcher.mock.calls[1][1].body).languages).toEqual(["en", "fr"]);
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a language" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
  expect((screen.getByRole("checkbox", { name: "English" }) as HTMLInputElement).checked).toBe(
    true,
  );
  expect((screen.getByRole("checkbox", { name: "French" }) as HTMLInputElement).checked).toBe(
    false,
  );
});
