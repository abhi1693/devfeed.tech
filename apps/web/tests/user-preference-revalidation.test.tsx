// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UserProvider, useUser } from "@/components/user-account";
import { FeedPreferencesProvider, useFeedPreferences } from "@/components/feed-preferences";
import { ScopedFeedPreferences } from "@/components/scoped-feed-preferences";
import { ThemePreferencesProvider, useThemePreferences } from "@/components/theme-preferences";
import { ColumnPreferencesProvider, useColumnPreferences } from "@/components/column-preferences";
import { TopicFollowsProvider } from "@/components/topic-follows";
import { TopicPreferences } from "@/components/topic-preferences";
import { useFollowPreferences } from "@/lib/use-follow-preferences";
import { beginReaderNavigation } from "@/lib/reader-navigation";
import { articlePreviewOpened } from "@/lib/reader-preview-history";
import type { Appearance, FeedDisplay, UserIdentity } from "@/lib/user";
import type { ReaderColumn } from "@/lib/reader-preferences";
import { topic } from "./fixtures";

const identity: UserIdentity = {
  user_id: "reader",
  csrf_token: "same-session",
  name: "Reader",
  email: null,
  expires_at: 4102444800,
};
const localTopic = { ...topic, id: "local-choice", name: "Local choice" };
const paths = [
  "settings/profile",
  "settings/feed",
  "settings/feed-view",
  "settings/appearance",
  "settings/columns",
  "preferences",
  "preferences/sources",
  "preferences/tags",
];

function GlobalState() {
  const { sessionRevision } = useUser();
  const { languages, content_types } = useFeedPreferences();
  const { theme, appearance } = useThemePreferences();
  const { columns } = useColumnPreferences();
  const sources = useFollowPreferences("source");
  const tags = useFollowPreferences("tag");
  return (
    <>
      <output aria-label="Preference revision">{sessionRevision}</output>
      <output aria-label="Feed preferences">
        {languages.join(",") + ":" + content_types.join(",")}
      </output>
      <output aria-label="Appearance preferences">
        {[theme, appearance.timezone, appearance.date_format, appearance.time_format].join(":")}
      </output>
      <output aria-label="Source preferences">{sources.ids.join(",")}</output>
      <output aria-label="Tag preferences">{tags.ids.join(",")}</output>
      <output aria-label="Column preferences">
        {columns.map(({ feed }) => feed.kind).join(",")}
      </output>
      <input aria-label="Retained reader state" defaultValue="initial" />
    </>
  );
}

function ScopedState() {
  return <output aria-label="Scoped layout">{useFeedPreferences().view}</output>;
}

function App({ refreshKey = 0 }: { refreshKey?: number }) {
  return (
    <UserProvider refreshKey={refreshKey}>
      <FeedPreferencesProvider>
        <ThemePreferencesProvider>
          <ColumnPreferencesProvider workspace="my_feed">
            <TopicFollowsProvider>
              <GlobalState />
              <ScopedFeedPreferences scope="latest">
                <ScopedState />
              </ScopedFeedPreferences>
              <TopicPreferences topics={[localTopic]} />
            </TopicFollowsProvider>
          </ColumnPreferencesProvider>
        </ThemePreferencesProvider>
      </FeedPreferencesProvider>
    </UserProvider>
  );
}

function transport() {
  let topics = ["initial-topic"];
  let sources = ["initial-source"];
  let tags = ["initial-tag"];
  let layout = "stacked";
  let columns: ReaderColumn[] = [
    { id: "11111111-1111-4111-8111-111111111111", feed: { kind: "latest" } },
  ];
  let feed: FeedDisplay = {
    view: "cards",
    columns: [],
    languages: ["en"],
    content_types: ["news"],
  };
  let appearance: Appearance = {
    theme: "light",
    timezone: "local",
    date_format: "locale",
    time_format: "system",
  };
  const authReplies: ((response: Response) => void)[] = [];
  let holdAuth = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), window.location.href).pathname.replace("/api/v1/user/", "");
    if (path === "auth/me") {
      if (holdAuth) return new Promise<Response>((resolve) => authReplies.push(resolve));
      return Response.json(identity);
    }
    if (path === "settings/profile") return Response.json({ display_name: null, avatar_url: null });
    if (path === "settings/feed") return Response.json(feed);
    if (path === "settings/feed-view") return Response.json({ view: layout });
    if (path === "settings/appearance") return Response.json(appearance);
    if (path === "settings/columns") return Response.json({ initialized: true, columns });
    if (path === "preferences") {
      if (init?.method === "PUT") topics = JSON.parse(String(init.body)).topic_ids;
      return Response.json({ topic_ids: topics });
    }
    if (path === "preferences/sources") return Response.json({ source_ids: sources });
    if (path === "preferences/tags") return Response.json({ tag_ids: tags });
    throw new Error(`Unexpected revalidation request: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    fetcher,
    reads(path: string) {
      return fetcher.mock.calls.filter(
        ([input, init]) =>
          new URL(String(input), window.location.href).pathname === `/api/v1/user/${path}` &&
          !init?.method,
      );
    },
    changeRemotely() {
      topics = ["initial-topic", "remote-topic-outside-page"];
      sources = ["remote-source"];
      tags = ["remote-tag"];
      layout = "compact";
      columns = [{ ...columns[0], feed: { kind: "recommended" } }];
      feed = { ...feed, languages: ["fr"], content_types: ["tutorial"] };
      appearance = { theme: "dark", timezone: "UTC", date_format: "iso", time_format: "24" };
    },
    holdAuth() {
      holdAuth = true;
      return authReplies;
    },
  };
}

async function ready() {
  await waitFor(() => expect(screen.getByLabelText("Scoped layout").textContent).toBe("stacked"));
  await screen.findByText("1 topics selected");
  expect(screen.getByLabelText("Feed preferences").textContent).toBe("en:news");
  expect(screen.getByLabelText("Source preferences").textContent).toBe("initial-source");
  expect(screen.getByLabelText("Tag preferences").textContent).toBe("initial-tag");
  expect(screen.getByLabelText("Column preferences").textContent).toBe("latest");
  expect(screen.getByLabelText("Appearance preferences").textContent).toBe(
    "light:local:locale:system",
  );
}

async function refreshed() {
  await waitFor(() => expect(screen.getByLabelText("Scoped layout").textContent).toBe("compact"));
  await screen.findByText("2 topics selected");
  expect(screen.getByLabelText("Feed preferences").textContent).toBe("fr:tutorial");
  expect(screen.getByLabelText("Source preferences").textContent).toBe("remote-source");
  expect(screen.getByLabelText("Tag preferences").textContent).toBe("remote-tag");
  expect(screen.getByLabelText("Column preferences").textContent).toBe("recommended");
  expect(screen.getByLabelText("Appearance preferences").textContent).toBe("dark:UTC:iso:24");
}

function closePreview() {
  act(() => {
    beginReaderNavigation("/articles/a-preview");
    window.history.pushState(null, "", "/articles/a-preview");
    window.dispatchEvent(new Event(articlePreviewOpened));
    window.history.replaceState(null, "", "/latest");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/latest");
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it.each(["focus", "visibility", "refreshKey", "history", "BFCache"] as const)(
  "refreshes the same session's actual preference caches after an ordinary %s check",
  async (reason) => {
    let now = Date.now();
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const api = transport();
    const app = render(<App />);
    await ready();
    const revision = Number(screen.getByLabelText("Preference revision").textContent);
    const input = screen.getByRole("textbox", { name: "Retained reader state" });
    fireEvent.change(input, { target: { value: "retained local state" } });
    api.changeRemotely();
    if (reason === "focus" || reason === "visibility") {
      now += 59_999;
      fireEvent.focus(window);
      expect(api.reads("auth/me")).toHaveLength(1);
      now += 2;
      if (reason === "focus") fireEvent.focus(window);
      else fireEvent(document, new Event("visibilitychange"));
    } else if (reason === "refreshKey") app.rerender(<App refreshKey={1} />);
    else if (reason === "history") fireEvent(window, new PopStateEvent("popstate"));
    else fireEvent(window, new PageTransitionEvent("pageshow", { persisted: true }));
    await refreshed();
    expect(api.reads("auth/me")).toHaveLength(2);
    for (const path of paths) expect(api.reads(path), path).toHaveLength(2);
    expect(Number(screen.getByLabelText("Preference revision").textContent)).toBe(revision + 1);
    expect(screen.getByRole("textbox", { name: "Retained reader state" })).toBe(input);
    expect(input).toHaveProperty("value", "retained local state");
    fireEvent.focus(window);
    expect(api.reads("auth/me")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Local choice" }));
    fireEvent.click(screen.getByRole("button", { name: "Save topics" }));
    await screen.findByText("Your topics are saved.");
    const writes = api.fetcher.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({
      topic_ids: ["initial-topic", "remote-topic-outside-page", "local-choice"],
    });
  },
);

it("does not refresh confirmed preference caches on retained preview returns", async () => {
  const api = transport();
  render(<App />);
  await ready();
  const revision = screen.getByLabelText("Preference revision").textContent;
  api.changeRemotely();
  closePreview();
  await waitFor(() => expect(api.reads("auth/me")).toHaveLength(2));
  closePreview();
  await waitFor(() => expect(api.reads("auth/me")).toHaveLength(3));
  await ready();
  expect(screen.getByLabelText("Preference revision").textContent).toBe(revision);
  for (const path of paths) expect(api.reads(path), path).toHaveLength(1);
});

it("carries an unfinished ordinary refresh into its retained-preview replacement and ignores the aborted reply", async () => {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const api = transport();
  render(<App />);
  await ready();
  const revision = Number(screen.getByLabelText("Preference revision").textContent);
  const pending = api.holdAuth();
  api.changeRemotely();
  now += 60_001;
  fireEvent.focus(window);
  await waitFor(() => expect(pending).toHaveLength(1));
  const ordinarySignal = api.reads("auth/me")[1][1]?.signal;
  closePreview();
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(ordinarySignal?.aborted).toBe(true);
  await act(async () => pending[1](Response.json(identity)));
  await refreshed();
  expect(Number(screen.getByLabelText("Preference revision").textContent)).toBe(revision + 1);
  for (const path of paths) expect(api.reads(path), path).toHaveLength(2);
  await act(async () => pending[0](Response.json({ ...identity, user_id: "previous-response" })));
  expect(Number(screen.getByLabelText("Preference revision").textContent)).toBe(revision + 1);
  expect(screen.getByLabelText("Source preferences").textContent).toBe("remote-source");
  for (const path of paths) expect(api.reads(path), path).toHaveLength(2);
});
