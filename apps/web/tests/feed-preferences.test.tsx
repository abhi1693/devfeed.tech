// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedPreferencesProvider, useFeedPreferences } from "@/components/feed-preferences";
import { contentTypes } from "@/lib/feed-query";
import { FeedSettings } from "@/components/feed-settings";
import { ArticleGrid } from "@/components/article-grid";
import { ArticleViewOptions } from "@/components/article-view-settings";
import { TrendingContent } from "@/components/trending-content";
import type { FeedPage } from "@/lib/types";
import type { UserIdentity } from "@/lib/user";
import { guestReaderPreferencesKey, type ReaderColumn } from "@/lib/reader-preferences";
import { useEffect, type ReactNode } from "react";
import { article } from "./fixtures";
const navigation = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("@/components/user-shell", () => ({
  UserShell: ({ children }: { children: ReactNode }) => children,
}));
// This suite exercises the legacy shared provider and its filter/column contracts.
// Scoped route persistence and Appearance integration have their own regression suites.
vi.mock("@/components/scoped-feed-preferences", () => ({
  ScopedFeedPreferences: ({ children }: { children: ReactNode }) => children,
  useFeedViewScope: () => null,
}));
const session = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
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
const workspaceColumns: ReaderColumn[] = [
  { id: "11111111-1111-4111-8111-111111111111", feed: { kind: "latest" } },
  {
    id: "22222222-2222-4222-8222-222222222222",
    feed: { kind: "topic", id: "55555555-5555-4555-8555-555555555555" },
  },
  { id: "33333333-3333-4333-8333-333333333333", feed: { kind: "recommended" } },
];
const accountPayload = (value: object) =>
  Object.fromEntries(Object.entries(value).filter(([key]) => key !== "columns"));
function workspaceHarness() {
  let preferences!: ReturnType<typeof useFeedPreferences>;
  function Workspace() {
    const current = useFeedPreferences();
    useEffect(() => {
      preferences = current;
    }, [current]);
    return null;
  }
  function WorkspaceApp({ children }: { children?: ReactNode }) {
    return (
      <FeedPreferencesProvider>
        <Workspace />
        {children}
      </FeedPreferencesProvider>
    );
  }
  return { WorkspaceApp, preferences: () => preferences };
}
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
function LayoutApp() {
  return (
    <FeedPreferencesProvider>
      <ArticleViewOptions />
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
  session.sessionRevision = 1;
  navigation.refresh.mockClear();
});

it.each([
  ["guest", "cards", "Grid"],
  ["guest", "compact", "List"],
  ["guest", "stacked", "Stacked"],
  ["guest", "columns", "Grid"],
  ["account", "cards", "Grid"],
  ["account", "compact", "List"],
  ["account", "stacked", "Stacked"],
  ["account", "columns", "Columns"],
])("restores %s preference %s using only available layouts", async (owner, view, selected) => {
  session.user = owner === "account" ? account : null;
  localStorage.setItem("devfeed:feed-view:guest", view);
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ view, content_types: [...contentTypes], languages: ["en"] }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<LayoutApp />);
  const selectedRadio = await screen.findByRole("radio", { name: selected });
  expect((selectedRadio as HTMLInputElement).checked).toBe(true);
  expect(screen.getAllByRole("radio").map((radio) => radio.getAttribute("aria-label"))).toEqual([
    "Grid",
    "List",
    "Stacked",
    "Columns",
  ]);
  if (selected === "Stacked")
    expect(screen.getAllByRole("region", { name: "Articles in stacked view" })).toHaveLength(2);
  expect(fetcher).toHaveBeenCalledTimes(owner === "account" ? 1 : 0);
});

it("keeps a guest's old Columns preference intact while offering Grid and requiring sign-in", async () => {
  const saved = JSON.stringify({
    version: 1,
    view: "columns",
    columns: workspaceColumns,
    columns_initialized: true,
  });
  localStorage.setItem(guestReaderPreferencesKey, saved);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<LayoutApp />);
  const columns = await screen.findByRole("radio", { name: "Columns" });
  expect(columns).toHaveProperty("disabled", true);
  expect(columns).toHaveProperty("checked", false);
  expect(screen.getByRole("radio", { name: "Grid" })).toHaveProperty("checked", true);
  expect(screen.getByText("Sign in to arrange My feed into columns.")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Sign in to use Columns" }).getAttribute("href"),
  ).toContain("/login");
  fireEvent.click(columns);
  expect(screen.getByRole("radio", { name: "Grid" })).toHaveProperty("checked", true);
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBe(saved);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each(["loading", "failed", "empty"])(
  "keeps Appearance layout settings independent while the trending feed is %s",
  async (state) => {
    const feed: PromiseSettledResult<FeedPage> | undefined =
      state === "loading"
        ? undefined
        : state === "failed"
          ? { status: "rejected", reason: new Error("unavailable") }
          : { status: "fulfilled", value: { items: [], next_cursor: null } };
    render(
      <FeedPreferencesProvider>
        <ArticleViewOptions />
        <TrendingContent feed={feed} />
      </FeedPreferencesProvider>,
    );
    const list = await screen.findByRole("radio", { name: "List" });
    expect(screen.queryByRole("group", { name: "Article view" })).toBeNull();
    await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
    fireEvent.click(list);
    expect((list as HTMLInputElement).checked).toBe(true);
    if (state === "loading") {
      expect(screen.getByRole("status", { name: "Loading trending articles…" })).toBeTruthy();
      expect(document.querySelector(".loading-skeleton.compact")).toBeTruthy();
    } else if (state === "failed") {
      expect(screen.getByRole("heading", { name: "Couldn’t load trending articles" })).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(navigation.refresh).toHaveBeenCalledOnce();
    } else {
      expect(screen.getByRole("heading", { name: "No trending articles yet" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    }
  },
);

it("switches a loaded trending feed without replacing its articles or fetching again", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ArticleViewOptions />
      <TrendingContent
        feed={{ status: "fulfilled", value: { items: [article], next_cursor: null } }}
      />
    </FeedPreferencesProvider>,
  );
  const grid = await screen.findByRole("radio", { name: "Grid" });
  await waitFor(() => expect(grid.hasAttribute("disabled")).toBe(false));
  expect((grid as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("link", { name: article.title })).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "List" }));
  expect(screen.getByRole("table", { name: "Articles in compact view" })).toBeTruthy();
  expect(screen.getByRole("link", { name: article.title }).getAttribute("href")).toBe(
    `/articles/${article.slug}`,
  );
  fireEvent.click(grid);
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("link", { name: article.title })).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("switches every loaded batch for guests and restores the choice after remounting", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const app = render(<LayoutApp />);
  const grid = await screen.findByRole("radio", { name: "Grid" });
  await waitFor(() => expect(grid.hasAttribute("disabled")).toBe(false));
  expect((grid as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: "List" }));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(screen.getByText("Second batch")).toBeTruthy();
  app.unmount();
  render(<LayoutApp />);
  await screen.findAllByRole("table");
  fireEvent.click(screen.getByRole("radio", { name: "Grid" }));
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
  render(<LayoutApp />);
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  expect(screen.getAllByRole("table")).toHaveLength(2);
  fireEvent.click(screen.getByRole("radio", { name: "Grid" }));
  expect(screen.queryByRole("table")).toBeNull();
});

it("keeps guest layout choices separate from account settings and ignores invalid values", async () => {
  localStorage.setItem("devfeed:feed-view:guest", "unknown");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ view: "cards" }));
  vi.stubGlobal("fetch", fetcher);
  const app = render(<LayoutApp />);
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(list);
  session.user = account;
  app.rerender(<LayoutApp />);
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  expect(fetcher).toHaveBeenCalledTimes(1);
  session.user = null;
  app.rerender(<LayoutApp />);
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
  render(<LayoutApp />);
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  fireEvent.click(list);
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(list.hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("radio", { name: "Grid" }).hasAttribute("disabled")).toBe(true);
  expect(
    screen.getByRole("region", { name: "Article feed layout" }).getAttribute("aria-busy"),
  ).toBe("true");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    method: "PUT",
    headers: { "X-CSRF-Token": "csrf" },
  });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    ...settings,
    view: "compact",
  });
  await act(async () => finish(Response.json({ ...settings, view: "compact" })));
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
});

it("rolls back a rejected Appearance layout save, announces it and allows retry", async () => {
  session.user = account;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ view: "cards" }))
      .mockResolvedValueOnce(Response.json({}, { status: 503 }))
      .mockResolvedValueOnce(
        Response.json({ view: "compact", content_types: [...contentTypes], languages: ["en"] }),
      ),
  );
  render(<LayoutApp />);
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
  fireEvent.click(list);
  await screen.findByRole("alert");
  expect(screen.queryByRole("table")).toBeNull();
  expect((screen.getByRole("radio", { name: "Grid" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(list);
  await screen.findAllByRole("table");
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

it.each(["account change", "credential refresh"])(
  "aborts an earlier layout save on %s and ignores its late success",
  async (change) => {
    session.user = account;
    const reloadedColumns = [workspaceColumns[2], workspaceColumns[0]];
    let finish!: (response: Response) => void;
    let writeSignal: AbortSignal | null | undefined;
    const events = vi.spyOn(window, "dispatchEvent");
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ view: "cards", columns: workspaceColumns }))
      .mockImplementationOnce((_url: string, init?: RequestInit) => {
        writeSignal = init?.signal;
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      })
      .mockResolvedValueOnce(Response.json({ view: "cards", columns: reloadedColumns }))
      .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
    vi.stubGlobal("fetch", fetcher);
    const app = render(<LayoutApp />);
    const list = await screen.findByRole("radio", { name: "List" });
    await waitFor(() => expect(list.hasAttribute("disabled")).toBe(false));
    fireEvent.click(list);
    session.user =
      change === "account change"
        ? { ...account, user_id: "second" }
        : { ...account, csrf_token: "fresh-csrf" };
    session.sessionRevision++;
    app.rerender(<LayoutApp />);
    expect(writeSignal?.aborted).toBe(true);
    await act(async () => {});
    await act(async () => finish(Response.json({ view: "compact", columns: workspaceColumns })));
    expect(screen.queryByRole("table")).toBeNull();
    expect((screen.getByRole("radio", { name: "Grid" }) as HTMLInputElement).checked).toBe(true);
    expect(events.mock.calls.filter(([event]) => event.type === "devfeed:analytics")).toHaveLength(
      0,
    );
    await act(async () => fireEvent.click(screen.getByRole("radio", { name: "List" })));
    expect(JSON.parse(fetcher.mock.calls[3][1].body)).not.toHaveProperty("columns");
    expect(fetcher.mock.calls[3][1].headers["X-CSRF-Token"]).toBe(
      change === "credential refresh" ? "fresh-csrf" : "csrf",
    );
  },
);

it("retries unavailable preferences before allowing a layout write", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(Response.json({ view: "compact" }));
  vi.stubGlobal("fetch", fetcher);
  render(<LayoutApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
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
it("keeps layout selection in Appearance while Feed settings saves content preferences", async () => {
  session.user = account;
  const settings = { view: "compact", content_types: ["news"], languages: ["en"] };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(settings))
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("checkbox", { name: "News" });
  expect(screen.queryByRole("radio")).toBeNull();
  expect(
    screen
      .getAllByRole("link")
      .some((link) => link.getAttribute("href") === "/settings/appearance"),
  ).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Articles" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/user/settings/feed");
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    method: "PUT",
    headers: { "X-CSRF-Token": "csrf" },
  });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    ...settings,
    content_types: ["article", "news"],
  });
});

it("preserves the global List layout when resetting content and language defaults", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ view: "compact", content_types: ["news"], languages: ["fr"] }),
    )
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("checkbox", { name: "News" });
  fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    view: "compact",
    content_types: [...contentTypes],
    languages: ["en"],
  });
  expect(screen.getAllByRole("table")).toHaveLength(2);
});

it("saves an existing content draft with the latest Appearance layout", async () => {
  session.user = account;
  const initial = { view: "cards", content_types: ["news"], languages: ["en"] };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(initial))
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ArticleViewOptions />
      <FeedSettings />
    </FeedPreferencesProvider>,
  );
  fireEvent.click(await screen.findByRole("checkbox", { name: "Articles" }));
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "List" })));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(fetcher.mock.calls.slice(1).map(([, init]) => JSON.parse(init.body))).toEqual([
    { ...initial, view: "compact" },
    { ...initial, view: "compact", content_types: ["article", "news"] },
  ]);
});

it("retains confirmed layout controls but disables writes during an account history check", async () => {
  session.user = account;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ view: "compact" })));
  const app = render(<LayoutApp />);
  const list = await screen.findByRole("radio", { name: "List" });
  expect((list as HTMLInputElement).checked).toBe(true);
  session.loading = true;
  app.rerender(<LayoutApp />);
  expect(screen.getByRole("radio", { name: "List" })).toBe(list);
  expect(screen.getAllByRole("radio").every((radio) => (radio as HTMLInputElement).disabled)).toBe(
    true,
  );
  expect(screen.getAllByRole("table")).toHaveLength(2);
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
      Response.json({
        view: "compact",
        content_types: ["article", "news", "tutorial"],
        languages: ["en"],
      }),
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

it("saves language codes in a deterministic order after adding an earlier code", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ view: "cards", content_types: [...contentTypes], languages: ["zh", "en"] }),
    )
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("checkbox", { name: "English" });
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a language" }), {
    target: { value: "German" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "German" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
  expect(JSON.parse(fetcher.mock.calls[1][1].body).languages).toEqual(["de", "en", "zh"]);
});

it("retains the confirmed owner's article DOM during history checks and clears it for another owner", async () => {
  session.user = account;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ view: "cards", languages: ["en"], content_types: [...contentTypes] }),
    ),
  );
  const app = () => (
    <FeedPreferencesProvider>
      <ArticleGrid articles={[article]} waitForPreferences />
    </FeedPreferencesProvider>
  );
  const view = render(app());
  const heading = await screen.findByRole("heading", { name: article.title });
  session.loading = true;
  view.rerender(app());
  expect(screen.getByRole("heading", { name: article.title })).toBe(heading);
  session.user = { ...account, user_id: "different-owner" };
  view.rerender(app());
  expect(screen.queryByRole("heading", { name: article.title })).toBeNull();
});

it.each(["stacked", "columns"])(
  "preserves saved %s workspace preferences while saving feed filters",
  async (view) => {
    session.user = account;
    const settings = {
      view,
      columns: workspaceColumns,
      content_types: ["news"],
      languages: ["en"],
    };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(settings))
      .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
    vi.stubGlobal("fetch", fetcher);
    render(<App />);
    await screen.findByRole("checkbox", { name: "News" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryAllByRole("region", { name: "Articles in stacked view" })).toHaveLength(
      view === "stacked" ? 2 : 0,
    );
    expect(screen.getByText(article.title)).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a language" }), {
      target: { value: "French" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "French" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save changes" })));
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      ...accountPayload(settings),
      languages: ["en", "fr"],
    });
    expect(screen.getByText("Feed settings saved.")).toBeTruthy();
  },
);

it.each(["compact", "stacked", "columns"])(
  "persists guest %s layout and column ordering across provider remounts",
  async (view) => {
    localStorage.setItem("devfeed:feed-view:guest", view);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const { WorkspaceApp, preferences } = workspaceHarness();
    let app = render(<WorkspaceApp />);
    await waitFor(() => expect(preferences().loading).toBe(false));
    await act(async () => expect(await preferences().setColumns(workspaceColumns)).toBe(true));
    app.unmount();
    app = render(<WorkspaceApp />);
    await waitFor(() => expect(preferences().loading).toBe(false));
    expect(preferences().view).toBe(view);
    expect(preferences().columns).toEqual(workspaceColumns);
    const reordered = [workspaceColumns[2], workspaceColumns[0]];
    await act(async () => expect(await preferences().setColumns(reordered)).toBe(true));
    app.unmount();
    render(<WorkspaceApp />);
    await waitFor(() => expect(preferences().loading).toBe(false));
    expect(preferences().columns).toEqual(reordered);
    expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!)).toEqual({
      version: 1,
      view,
      columns: reordered,
      columns_initialized: true,
    });
    expect(fetcher).not.toHaveBeenCalled();
  },
);

it("rolls back rejected column saves without losing the stored layout or existing workspace", async () => {
  session.user = account;
  const settings = {
    view: "columns",
    columns: workspaceColumns,
    content_types: ["news"],
    languages: ["en"],
  };
  let finish!: (response: Response) => void;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(settings))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    )
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  const reordered = [workspaceColumns[2], workspaceColumns[0]];
  let pending!: Promise<boolean>;
  act(() => {
    pending = preferences().setColumns(reordered);
  });
  expect(preferences().columns).toEqual(reordered);
  expect(preferences().storedView).toBe("columns");
  expect(preferences().busy).toBe(true);
  await act(async () => {
    finish(Response.json({}, { status: 503 }));
    expect(await pending).toBe(false);
  });
  expect(preferences().columns).toEqual(workspaceColumns);
  expect(preferences().storedView).toBe("columns");
  expect(preferences().busy).toBe(false);
  expect(preferences().error).toBe("Couldn’t save your feed settings. Please try again.");
  await act(async () => expect(await preferences().setColumns(reordered)).toBe(true));
  expect(preferences().columns).toEqual(reordered);
  expect(preferences().error).toBe("");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ ...settings, columns: reordered });
});

it("blocks guest writes for an unknown storage version without overwriting its data", async () => {
  const newerWorkspace = JSON.stringify({ version: 2, view: "columns", columns: workspaceColumns });
  localStorage.setItem(guestReaderPreferencesKey, newerWorkspace);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(
    <WorkspaceApp>
      <ArticleViewOptions />
    </WorkspaceApp>,
  );
  await screen.findByRole("heading", { name: "Couldn’t load article layout" });
  expect(preferences().unavailable).toBe(true);
  expect(screen.queryByRole("radio")).toBeNull();
  await act(async () => {
    expect(await preferences().setView("compact")).toBe(false);
    expect(await preferences().setColumns([])).toBe(false);
    expect(await preferences().save({ languages: ["fr"] })).toBe(false);
  });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(preferences().unavailable).toBe(true));
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBe(newerWorkspace);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each(["guest", "account"])(
  "rejects duplicate feed selections before optimistic state or persistence for %s",
  async (owner) => {
    session.user = owner === "account" ? account : null;
    const settings = {
      view: "columns",
      columns: workspaceColumns,
      content_types: ["news"],
      languages: ["en"],
    };
    if (owner === "guest")
      localStorage.setItem(
        guestReaderPreferencesKey,
        JSON.stringify({ version: 1, ...settings, columns_initialized: true }),
      );
    const fetcher = vi.fn().mockResolvedValue(Response.json(settings));
    vi.stubGlobal("fetch", fetcher);
    const { WorkspaceApp, preferences } = workspaceHarness();
    render(<WorkspaceApp />);
    await waitFor(() => expect(preferences().loading).toBe(false));
    const original = preferences().columns;
    const originalStorage = localStorage.getItem(guestReaderPreferencesKey);
    const selected = workspaceColumns[1].feed;
    if (!("id" in selected)) throw new Error("Expected a catalog fixture");
    const duplicate = [
      ...workspaceColumns,
      {
        id: "66666666-6666-4666-8666-666666666666",
        feed: { ...selected, id: selected.id.replaceAll("-", "").toUpperCase() },
      },
    ];
    await act(async () => expect(await preferences().setColumns(duplicate)).toBe(false));
    await act(async () =>
      expect(await preferences().initializeColumns(duplicate, "restore_empty")).toBe(false),
    );
    expect(preferences().columns).toBe(original);
    expect(preferences().busy).toBe(false);
    expect(localStorage.getItem(guestReaderPreferencesKey)).toBe(originalStorage);
    expect(fetcher).toHaveBeenCalledTimes(owner === "account" ? 1 : 0);
  },
);

it("uses a new account's defaults and restores the complete guest workspace after sign-out", async () => {
  const guestWorkspace = { version: 1, view: "compact", columns: workspaceColumns };
  localStorage.setItem(guestReaderPreferencesKey, JSON.stringify(guestWorkspace));
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      view: "cards",
      columns: [],
      content_types: [...contentTypes],
      languages: ["en"],
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  const app = render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columns).toEqual(workspaceColumns);
  session.user = account;
  app.rerender(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().view).toBe("cards");
  expect(preferences().columns).toEqual([]);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!)).toEqual(guestWorkspace);
  session.user = null;
  app.rerender(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().view).toBe("compact");
  expect(preferences().columns).toEqual(workspaceColumns);
  expect(fetcher).toHaveBeenCalledOnce();
});

it("ignores an old account's delayed preference load after another account has loaded", async () => {
  session.user = account;
  let finish!: (response: Response) => void;
  let loadSignal: AbortSignal | null | undefined;
  const reloadedColumns = [workspaceColumns[2], workspaceColumns[0]];
  const secondSettings = {
    view: "columns",
    columns: reloadedColumns,
    content_types: ["news"],
    languages: ["fr"],
  };
  const fetcher = vi
    .fn()
    .mockImplementationOnce((_url: string, init?: RequestInit) => {
      loadSignal = init?.signal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    })
    .mockResolvedValueOnce(Response.json(secondSettings))
    .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  const app = render(<WorkspaceApp />);
  expect(preferences().loading).toBe(true);
  session.user = { ...account, user_id: "second", csrf_token: "second-csrf" };
  session.sessionRevision++;
  app.rerender(<WorkspaceApp />);
  expect(loadSignal?.aborted).toBe(true);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columns).toEqual(reloadedColumns);
  await act(async () =>
    finish(Response.json({ view: "compact", columns: workspaceColumns, languages: ["en"] })),
  );
  expect(preferences().view).toBe("columns");
  expect(preferences().storedView).toBe("columns");
  expect(preferences().columns).toEqual(reloadedColumns);
  expect(preferences().languages).toEqual(["fr"]);
  await act(async () => expect(await preferences().save({ languages: ["en"] })).toBe(true));
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
    ...accountPayload(secondSettings),
    languages: ["en"],
  });
  expect(fetcher.mock.calls[2][1].headers["X-CSRF-Token"]).toBe("second-csrf");
});

it.each(["explicit refresh", "credential refresh"])(
  "blocks writes during a same-account %s and saves against the reloaded workspace",
  async (refresh) => {
    session.user = account;
    const initial = {
      view: "cards",
      columns: workspaceColumns,
      content_types: ["news"],
      languages: ["en"],
    };
    const reloaded = {
      view: "cards",
      columns: [workspaceColumns[2], workspaceColumns[0]],
      content_types: ["tutorial"],
      languages: ["fr"],
    };
    let finish!: (response: Response) => void;
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(initial))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      )
      .mockImplementation((_url, init) => Promise.resolve(Response.json(JSON.parse(init.body))));
    vi.stubGlobal("fetch", fetcher);
    const { WorkspaceApp, preferences } = workspaceHarness();
    const app = () => (
      <WorkspaceApp>
        <ArticleViewOptions />
        <ArticleGrid articles={[article]} waitForPreferences />
      </WorkspaceApp>
    );
    const mounted = render(app());
    const heading = await screen.findByRole("heading", { name: article.title });
    await waitFor(() => expect(preferences().loading).toBe(false));
    if (refresh === "explicit refresh") act(() => preferences().refresh());
    else {
      session.user = { ...account, csrf_token: "fresh-csrf" };
      session.sessionRevision++;
      mounted.rerender(app());
    }
    await waitFor(() => expect(preferences().busy).toBe(true));
    expect(preferences().loading).toBe(false);
    expect(screen.getByRole("heading", { name: article.title })).toBe(heading);
    expect(screen.getByRole("radio", { name: "List" }).hasAttribute("disabled")).toBe(true);
    await act(async () => {
      expect(await preferences().setView("compact")).toBe(false);
      expect(await preferences().setColumns([])).toBe(false);
      expect(await preferences().save({ languages: ["de"] })).toBe(false);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.every(([, init]) => !init.method)).toBe(true);
    expect(preferences().columns).toEqual(workspaceColumns);
    await act(async () => finish(Response.json(reloaded)));
    expect(preferences().busy).toBe(false);
    expect(preferences().columns).toEqual(reloaded.columns);
    expect(preferences().languages).toEqual(["fr"]);
    await act(async () => expect(await preferences().setView("compact")).toBe(true));
    expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
      ...accountPayload(reloaded),
      view: "compact",
    });
    expect(fetcher.mock.calls[2][1].headers["X-CSRF-Token"]).toBe(
      refresh === "credential refresh" ? "fresh-csrf" : "csrf",
    );
    const reordered = [...reloaded.columns].reverse();
    await act(async () => expect(await preferences().setColumns(reordered)).toBe(true));
    expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual({
      ...reloaded,
      view: "compact",
      columns: reordered,
    });
  },
);

it("preserves guest first-use eligibility through layout saves and records an explicit empty workspace", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  const app = render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columnsInitialized).toBe(false);
  await act(async () => expect(await preferences().setView("stacked")).toBe(true));
  expect(preferences().columnsInitialized).toBe(false);
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!).columns_initialized).toBe(
    false,
  );
  await act(async () => expect(await preferences().setColumns([])).toBe(true));
  expect(preferences().columnsInitialized).toBe(true);
  app.unmount();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columnsInitialized).toBe(true);
  expect(preferences().columns).toEqual([]);
  expect(fetcher).not.toHaveBeenCalled();
});

it("preserves a cleared guest workspace automatically and restores starters only on explicit request", async () => {
  localStorage.setItem(
    guestReaderPreferencesKey,
    JSON.stringify({ version: 1, view: "columns", columns: [] }),
  );
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columnsInitialized).toBe(true);
  await act(async () =>
    expect(await preferences().initializeColumns(workspaceColumns, "initialize")).toBe(true),
  );
  expect(preferences().columns).toEqual([]);
  await act(async () =>
    expect(await preferences().initializeColumns(workspaceColumns, "restore_empty")).toBe(true),
  );
  expect(preferences().columns).toEqual(workspaceColumns);
  expect(preferences().columnsInitialized).toBe(true);
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!).columns_initialized).toBe(
    true,
  );
  expect(fetcher).not.toHaveBeenCalled();
});

it("installs authoritative account columns from the atomic receipt while preserving layout and filters", async () => {
  session.user = account;
  const settings = { view: "compact", columns: [], languages: ["fr"], content_types: ["tutorial"] };
  const concurrentColumns = [workspaceColumns[2]];
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(settings))
    .mockResolvedValueOnce(Response.json({ columns: concurrentColumns, initialized: true }));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columnsInitialized).toBeNull();
  expect(fetcher).toHaveBeenCalledOnce();
  await act(async () =>
    expect(await preferences().initializeColumns(workspaceColumns, "initialize")).toBe(true),
  );
  expect(preferences().columns).toEqual(concurrentColumns);
  expect(preferences().columnsInitialized).toBe(true);
  expect(preferences().view).toBe("compact");
  expect(preferences().languages).toEqual(["fr"]);
  expect(preferences().content_types).toEqual(["tutorial"]);
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/user/settings/columns");
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    method: "PUT",
    headers: { "X-CSRF-Token": "csrf" },
  });
  expect(JSON.parse(String(fetcher.mock.calls[1][1].body))).toEqual({
    columns: workspaceColumns,
    mode: "initialize",
  });
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBeNull();
});

it("keeps an initialized empty account empty when automatic initialization loses the server race", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "columns", columns: [] }))
    .mockResolvedValueOnce(Response.json({ columns: [], initialized: true }));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  expect(preferences().columnsInitialized).toBeNull();
  await act(async () =>
    expect(await preferences().initializeColumns(workspaceColumns, "initialize")).toBe(true),
  );
  expect(preferences().columnsInitialized).toBe(true);
  expect(preferences().columns).toEqual([]);
});

it("does not claim initialization after an invalid or failed account receipt and permits retry", async () => {
  session.user = account;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "columns", columns: [] }))
    .mockResolvedValueOnce(Response.json({ columns: workspaceColumns }))
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ columns: workspaceColumns, initialized: true }));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  for (let attempt = 0; attempt < 2; attempt++) {
    await act(async () =>
      expect(await preferences().initializeColumns(workspaceColumns, "initialize")).toBe(false),
    );
    expect(preferences().columnsInitialized).toBeNull();
    expect(preferences().columns).toEqual([]);
    expect(preferences().error).toContain("Couldn’t save your starter columns");
  }
  await act(async () =>
    expect(await preferences().initializeColumns(workspaceColumns, "initialize")).toBe(true),
  );
  expect(preferences().columnsInitialized).toBe(true);
  expect(preferences().columns).toEqual(workspaceColumns);
  expect(preferences().error).toBe("");
});

it("cancels a starter request when its owner changes and ignores the old account's receipt", async () => {
  session.user = account;
  let finish!: (response: Response) => void;
  let signal: AbortSignal | null | undefined;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "columns", columns: [] }))
    .mockImplementationOnce((_url: string, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    })
    .mockResolvedValueOnce(Response.json({ view: "compact", columns: [workspaceColumns[2]] }));
  vi.stubGlobal("fetch", fetcher);
  const { WorkspaceApp, preferences } = workspaceHarness();
  const app = render(<WorkspaceApp />);
  await waitFor(() => expect(preferences().loading).toBe(false));
  let pending!: Promise<boolean>;
  act(() => {
    pending = preferences().initializeColumns(workspaceColumns, "initialize");
  });
  session.user = { ...account, user_id: "second", csrf_token: "second-csrf" };
  session.sessionRevision++;
  app.rerender(<WorkspaceApp />);
  expect(signal?.aborted).toBe(true);
  await waitFor(() => expect(preferences().view).toBe("compact"));
  await act(async () => {
    finish(Response.json({ columns: workspaceColumns, initialized: true }));
    expect(await pending).toBe(false);
  });
  expect(preferences().columns).toEqual([workspaceColumns[2]]);
  expect(preferences().columnsInitialized).toBe(true);
  expect(preferences().view).toBe("compact");
});
