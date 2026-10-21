// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { contentTypes } from "@/lib/feed-query";
import { FeedSettings } from "@/components/feed-settings";
import { ArticleGrid } from "@/components/article-grid";
import { ArticleViewSettings } from "@/components/article-view-settings";
import { TrendingContent } from "@/components/trending-content";
import type { FeedPage } from "@/lib/types";
import type { UserIdentity } from "@/lib/user";
import type { ReactNode } from "react";
import { article } from "./fixtures";
const navigation = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("@/components/user-shell", () => ({
  UserShell: ({ children }: { children: ReactNode }) => children,
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
      <ArticleViewSettings />
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
  ["guest", "stacked", "Grid"],
  ["guest", "columns", "Grid"],
  ["account", "cards", "Grid"],
  ["account", "compact", "List"],
  ["account", "stacked", "Grid"],
  ["account", "columns", "Grid"],
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
  ]);
  expect(screen.queryByRole("radio", { name: "Stacked" })).toBeNull();
  expect(screen.queryByRole("radio", { name: "Columns" })).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(owner === "account" ? 1 : 0);
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
        <ArticleViewSettings />
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
      <ArticleViewSettings />
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
    body: JSON.stringify({ ...settings, view: "compact" }),
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
    let finish!: (response: Response) => void;
    let writeSignal: AbortSignal | null | undefined;
    const events = vi.spyOn(window, "dispatchEvent");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ view: "cards" }))
        .mockImplementationOnce((_url: string, init?: RequestInit) => {
          writeSignal = init?.signal;
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        })
        .mockResolvedValueOnce(Response.json({ view: "cards" })),
    );
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
    await act(async () => finish(Response.json({ view: "compact" })));
    expect(screen.queryByRole("table")).toBeNull();
    expect((screen.getByRole("radio", { name: "Grid" }) as HTMLInputElement).checked).toBe(true);
    expect(events.mock.calls.filter(([event]) => event.type === "devfeed:analytics")).toHaveLength(
      0,
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
    body: JSON.stringify({ ...settings, content_types: ["article", "news"] }),
    headers: { "X-CSRF-Token": "csrf" },
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
      <ArticleViewSettings />
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
