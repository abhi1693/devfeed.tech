// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ColumnPreferencesProvider, useColumnPreferences } from "@/components/column-preferences";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { StarterColumns } from "@/components/starter-columns";
import { createColumnRequestQueue } from "@/lib/column-feed";
import { guestReaderPreferencesKey, type ReaderColumn } from "@/lib/reader-preferences";
import type { ReaderColumnsState } from "@/lib/user-mutations";
import type { UserIdentity } from "@/lib/user";

const session = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
const uuid = (n: number) => `aaaaaaaa-aaaa-4aaa-8aaa-${n.toString(16).padStart(12, "0")}`;
const latest: ReaderColumn[] = [{ id: uuid(1), feed: { kind: "latest" } }];
const custom: ReaderColumn[] = [{ id: uuid(2), feed: { kind: "tag", id: uuid(20) } }];
const suggested = [
  { kind: "topic", id: uuid(10) },
  { kind: "source", id: uuid(11) },
  { kind: "tag", id: uuid(12) },
  { kind: "topic", id: uuid(13) },
];
const account = (id: string): UserIdentity => ({
  user_id: id,
  csrf_token: `csrf-${id}`,
  name: id,
  email: null,
  expires_at: 4102444800,
});
function transport(initial: ReaderColumnsState = { initialized: false, columns: [] }) {
  const personal = new Map<string, ReaderColumnsState>([["a", structuredClone(initial)]]);
  const writes: { owner: string; body: { columns: ReaderColumn[]; mode: string } }[] = [];
  const reads: string[] = [];
  let feeds: unknown = suggested;
  let hold: Promise<Response> | null = null;
  let fail = false;
  let languages = ["en"];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href);
    const owner = session.user?.user_id ?? "guest";
    reads.push(url.href);
    if (url.pathname.endsWith("/settings/feed")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        expect(body.columns).toBeUndefined();
        languages = body.languages;
      }
      return Response.json({
        view: "columns",
        columns: latest,
        languages,
        content_types: ["news"],
      });
    }
    if (url.pathname.endsWith("/columns/starters")) {
      if (hold) return hold;
      return fail ? Response.json({}, { status: 503 }) : Response.json({ feeds });
    }
    if (url.pathname.endsWith("/settings/columns")) {
      expect(url.searchParams.get("workspace")).toBe("my_feed");
      const current = personal.get(owner) ?? { initialized: false, columns: [] };
      if (init?.method === "PUT") {
        expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(`csrf-${owner}`);
        const body = JSON.parse(String(init.body));
        writes.push({ owner, body });
        if (
          body.mode === "replace" ||
          (!current.columns.length && (body.mode === "restore_empty" || !current.initialized))
        ) {
          personal.set(owner, { initialized: true, columns: body.columns });
        }
      }
      return Response.json(personal.get(owner) ?? current);
    }
    throw new Error(`Unexpected personal columns request: ${url.href}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    fetcher,
    reads,
    writes,
    personal,
    setFeeds: (value: unknown) => {
      feeds = value;
    },
    fail: (value: boolean) => {
      fail = value;
    },
    hold: () => {
      let resolve!: (value: Response) => void;
      hold = new Promise((done) => {
        resolve = done;
      });
      return async () => {
        await act(async () => resolve(Response.json({ feeds })));
        hold = null;
      };
    },
  };
}
const queue = createColumnRequestQueue();
type PreferencesObserver = (state: ReturnType<typeof useColumnPreferences>) => void;
function Workspace({ observe }: { observe?: PreferencesObserver }) {
  const state = useColumnPreferences();
  observe?.(state);
  return (
    <>
      <output aria-label="My columns">{JSON.stringify(state.columns)}</output>
      <output aria-label="Workspace scope">{state.workspace}</output>
      <output aria-label="Languages">{state.languages.join(",")}</output>
      <button onClick={() => void state.save({ languages: ["fr"] })}>Change filters</button>
      <button disabled={state.loading || state.busy} onClick={() => void state.setColumns(custom)}>
        Choose mine
      </button>
      <button disabled={state.loading || state.busy} onClick={() => void state.setColumns([])}>
        Clear mine
      </button>
      <button
        disabled={state.loading || state.busy}
        onClick={() => {
          const first = state.columns[0];
          if (!first) return;
          const feed =
            "id" in first.feed ? { ...first.feed, id: first.feed.id.toUpperCase() } : first.feed;
          void state.setColumns([...state.columns, { id: uuid(99), feed }]);
        }}
      >
        Duplicate mine
      </button>
      {!!state.error && <p role="alert">{state.error}</p>}
      {!state.loading && !state.unavailable && !state.columns.length && (
        <StarterColumns queue={queue} />
      )}
    </>
  );
}
function App({ observe }: { observe?: PreferencesObserver }) {
  return (
    <FeedPreferencesProvider>
      <ColumnPreferencesProvider workspace="my_feed">
        <Workspace observe={observe} />
      </ColumnPreferencesProvider>
    </FeedPreferencesProvider>
  );
}
const columns = () =>
  JSON.parse(screen.getByLabelText("My columns").textContent!) as ReaderColumn[];
beforeEach(() => {
  session.user = account("a");
  session.loading = false;
  session.sessionRevision++;
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("starts My feed from its personal signals without replacing Latest or guest columns", async () => {
  const api = transport();
  localStorage.setItem(guestReaderPreferencesKey, "guest-owned");
  render(<App />);
  await waitFor(() => expect(columns()).toHaveLength(5));
  expect(columns().map((column) => column.feed)).toEqual([{ kind: "recommended" }, ...suggested]);
  expect(new Set(columns().map((column) => column.id)).size).toBe(5);
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].body.mode).toBe("initialize");
  expect(
    api.fetcher.mock.calls.filter(
      ([input, init]) =>
        String(input).includes("/settings/columns?workspace=my_feed") && !init?.method,
    ),
  ).toHaveLength(1);
  expect(api.reads.some((url) => new URL(url).pathname === "/api/v1/topics")).toBe(false);
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBe("guest-owned");
});

it("uses Recommended and Latest when there are no eligible follows or liked topics", async () => {
  const api = transport();
  api.setFeeds([]);
  render(<App />);
  await waitFor(() =>
    expect(columns().map((column) => column.feed)).toEqual([
      { kind: "recommended" },
      { kind: "latest" },
    ]),
  );
});

it("rejects duplicate My feed selections before sending a scoped save", async () => {
  const api = transport({ initialized: true, columns: custom });
  render(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Duplicate mine" })));
  expect(columns()).toEqual(custom);
  expect(api.writes).toEqual([]);
  expect(api.personal.get("a")).toEqual({ initialized: true, columns: custom });
});

it("preserves configured columns and intentional clearing until starters are explicitly requested", async () => {
  const api = transport({ initialized: true, columns: custom });
  render(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Clear mine" }));
  await screen.findByRole("button", { name: "Use starter columns" });
  expect(columns()).toEqual([]);
  expect(api.writes).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Use starter columns" }));
  await waitFor(() => expect(columns()).toHaveLength(5));
  expect(api.writes.at(-1)?.body.mode).toBe("restore_empty");
});

it("keeps a manual selection when personal discovery finishes late", async () => {
  const api = transport();
  const release = api.hold();
  render(<App />);
  await screen.findByText("Preparing starter columns…");
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "Choose mine" }));
  await waitFor(() => expect(columns()).toEqual(custom));
  await release();
  expect(columns()).toEqual(custom);
  expect(api.writes).toHaveLength(1);
});

it.each([
  { action: "selection", expected: custom },
  { action: "intentional clearing", expected: [] },
])("ignores a stale initializer after a manual $action is acknowledged", async ({ expected }) => {
  const api = transport();
  const release = api.hold();
  let preferences!: ReturnType<typeof useColumnPreferences>;
  render(<App observe={(state) => (preferences = state)} />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  // Discovery retains this actual context callback while its request is pending.
  const initializeDiscovered = preferences.initializeColumns;
  await act(async () => {
    expect(await preferences.setColumns(expected)).toBe(true);
    expect(columns()).toEqual([]);
    expect(await initializeDiscovered(latest, "initialize")).toBe(true);
  });
  await release();

  expect(columns()).toEqual(expected);
  expect(api.personal.get("a")).toEqual({ initialized: true, columns: expected });
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].body.mode).toBe("replace");
});

it("keeps a local replacement while stale explicit starter preparation is finishing", async () => {
  const api = transport();
  const release = api.hold();
  let preferences!: ReturnType<typeof useColumnPreferences>;
  render(<App observe={(state) => (preferences = state)} />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  const restoreDiscovered = preferences.initializeColumns;
  await act(async () => {
    expect(await preferences.setColumns(custom)).toBe(true);
    expect(await restoreDiscovered(latest, "restore_empty")).toBe(true);
  });
  await release();
  expect(columns()).toEqual(custom);
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].body.mode).toBe("replace");
});

it("does not submit starters while a manual replacement is awaiting acknowledgement", async () => {
  const api = transport();
  const releaseDiscovery = api.hold();
  let preferences!: ReturnType<typeof useColumnPreferences>;
  render(<App observe={(state) => (preferences = state)} />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  const initializeDiscovered = preferences.initializeColumns;
  const original = fetch;
  let releaseSave!: () => void;
  const heldSave = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  let started = false;
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/settings/columns?workspace=my_feed") && init?.method === "PUT") {
      started = true;
      await heldSave;
    }
    return original(input, init);
  });
  let saved!: Promise<boolean>;
  await act(async () => {
    saved = preferences.setColumns(custom);
  });
  await waitFor(() => expect(started).toBe(true));
  await act(async () => {
    expect(await initializeDiscovered(latest, "initialize")).toBe(false);
  });
  expect(api.writes).toEqual([]);
  await act(async () => {
    releaseSave();
    expect(await saved).toBe(true);
  });
  await releaseDiscovery();
  expect(columns()).toEqual(custom);
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].body.mode).toBe("replace");
});

it("keeps another device's columns when personal discovery finishes late", async () => {
  const api = transport();
  const release = api.hold();
  render(<App />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  api.personal.set("a", { initialized: true, columns: custom });
  await release();
  await waitFor(() => expect(columns()).toEqual(custom));
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].body.mode).toBe("initialize");
  expect(api.personal.get("a")).toEqual({ initialized: true, columns: custom });
});

it("keeps personal discovery alive when profile labels change", async () => {
  const api = transport();
  const release = api.hold();
  const app = render(<App />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  const reads = api.reads.length;
  session.user = { ...session.user!, name: "Renamed reader", email: "reader@example.test" };
  await act(async () => app.rerender(<App />));
  expect(api.reads).toHaveLength(reads);
  await release();
  await waitFor(() => expect(columns()).toHaveLength(5));
  expect(api.writes).toHaveLength(1);
});

it("does not reread columns for profile labels but refreshes a changed session revision", async () => {
  const api = transport({ initialized: true, columns: custom });
  const app = render(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  const reads = api.reads.length;
  session.user = { ...session.user!, name: "Renamed reader", email: "reader@example.test" };
  await act(async () => app.rerender(<App />));
  expect(api.reads).toHaveLength(reads);
  expect(columns()).toEqual(custom);

  api.personal.set("a", { initialized: true, columns: latest });
  session.sessionRevision++;
  app.rerender(<App />);
  await waitFor(() => expect(columns()).toEqual(latest));
  expect(
    api.fetcher.mock.calls.filter(
      ([input, init]) =>
        String(input).includes("/settings/columns?workspace=my_feed") && !init?.method,
    ),
  ).toHaveLength(2);
});

it("keeps an in-flight column save alive when profile labels change", async () => {
  const api = transport({ initialized: true, columns: latest });
  const app = render(<App />);
  await waitFor(() => expect(columns()).toEqual(latest));
  const original = fetch;
  let saveSignal: AbortSignal | undefined;
  let release!: () => void;
  const held = new Promise<void>((done) => {
    release = done;
  });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/settings/columns?workspace=my_feed") && init?.method === "PUT") {
      saveSignal = init.signal ?? undefined;
      await held;
    }
    return original(input, init);
  });
  fireEvent.click(screen.getByRole("button", { name: "Choose mine" }));
  await waitFor(() => expect(saveSignal).toBeDefined());
  const reads = api.reads.length;
  session.user = { ...session.user!, name: "Renamed reader", email: "reader@example.test" };
  await act(async () => app.rerender(<App />));
  expect(saveSignal!.aborted).toBe(false);
  expect(api.reads).toHaveLength(reads);
  await act(async () => release());
  await waitFor(() => expect(columns()).toEqual(custom));
  expect(api.writes).toHaveLength(1);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("offers retry without installing generic topics when personal discovery fails", async () => {
  const api = transport();
  api.fail(true);
  render(<App />);
  const retry = await screen.findByRole("button", { name: "Try again" });
  expect(api.writes).toHaveLength(0);
  api.fail(false);
  fireEvent.click(retry);
  await waitFor(() => expect(columns()).toHaveLength(5));
});

it.each([
  { feeds: [{ kind: "latest" }] },
  { feeds: [{ kind: "topic", id: "bad" }] },
  { feeds: [...suggested, suggested[0]] },
])("rejects malformed or oversized personal starter receipts $feeds", async ({ feeds }) => {
  const api = transport();
  api.setFeeds(feeds);
  render(<App />);
  await screen.findByRole("button", { name: "Try again" });
  expect(api.writes).toHaveLength(0);
});

it("uses the guest Latest workspace without reading any private preferences", async () => {
  const api = transport();
  session.user = null;
  localStorage.setItem(
    guestReaderPreferencesKey,
    JSON.stringify({ version: 1, view: "columns", columns: latest, columns_initialized: true }),
  );
  render(<App />);
  await waitFor(() => expect(columns()).toEqual(latest));
  expect(screen.getByLabelText("Workspace scope").textContent).toBe("latest");
  expect(api.reads).toEqual([]);
});

it("keeps My feed columns when a global filter acknowledgement contains Latest columns", async () => {
  const api = transport({ initialized: true, columns: custom });
  render(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  fireEvent.click(screen.getByRole("button", { name: "Change filters" }));
  await waitFor(() => expect(screen.getByLabelText("Languages").textContent).toBe("fr"));
  expect(columns()).toEqual(custom);
  expect(api.writes).toEqual([]);
});

it("disables edits while the same account's workspace is being rechecked", async () => {
  transport({ initialized: true, columns: custom });
  const app = render(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  const original = fetch;
  let started = false;
  let release!: () => void;
  const held = new Promise<void>((done) => {
    release = done;
  });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/settings/columns?workspace=my_feed") && !init?.method) {
      started = true;
      await held;
    }
    return original(input, init);
  });
  session.loading = true;
  app.rerender(<App />);
  session.loading = false;
  app.rerender(<App />);
  await waitFor(() => expect(started).toBe(true));
  expect(screen.getByRole("button", { name: "Choose mine" })).toHaveProperty("disabled", true);
  await act(async () => release());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Choose mine" })).toHaveProperty("disabled", false),
  );
  expect(columns()).toEqual(custom);
});

it("discards old account discovery and never copies it into the next account", async () => {
  const api = transport();
  const release = api.hold();
  const app = render(<App />);
  await waitFor(() =>
    expect(api.reads.some((url) => url.endsWith("/columns/starters"))).toBe(true),
  );
  api.personal.set("b", { initialized: true, columns: custom });
  session.user = account("b");
  session.sessionRevision++;
  app.rerender(<App />);
  await waitFor(() => expect(columns()).toEqual(custom));
  await release();
  expect(columns()).toEqual(custom);
  expect(api.writes).toHaveLength(0);
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBeNull();
});
