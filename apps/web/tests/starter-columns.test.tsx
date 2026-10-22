// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StarterColumns } from "@/components/starter-columns";
import { FeedPreferencesProvider, useFeedPreferences } from "@/components/feed-preferences";
import { createColumnRequestQueue, type ColumnRequestQueue } from "@/lib/column-feed";
import { guestReaderPreferencesKey, type ReaderColumn } from "@/lib/reader-preferences";
import type { UserIdentity } from "@/lib/user";

const session = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
const uuid = (index: number) => `aaaaaaaa-aaaa-4aaa-8aaa-${index.toString(16).padStart(12, "0")}`;
const account: UserIdentity = {
  user_id: "reader-a",
  csrf_token: "csrf-a",
  expires_at: 4102444800,
  name: "Reader A",
  email: null,
};
const chosen: ReaderColumn = { id: uuid(8), feed: { kind: "latest" } };
const topics = [
  { id: uuid(101), name: "TypeScript" },
  { id: uuid(102), name: "Python" },
  { id: uuid(103), name: "Rust" },
];

function transport(initialized = false) {
  const workspaces = new Map<string, { columns: ReaderColumn[]; initialized: boolean }>();
  const writes: { owner: string; columns: ReaderColumn[]; mode: string }[] = [];
  const requests: { url: URL; signal: AbortSignal | null | undefined }[] = [];
  let topicsResult: ((signal: AbortSignal | null | undefined) => Promise<Response>) | null = null;
  const current = () => {
    const owner = session.user?.user_id ?? "guest";
    if (!workspaces.has(owner)) workspaces.set(owner, { columns: [], initialized });
    return workspaces.get(owner)!;
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href);
    requests.push({ url, signal: init?.signal });
    if (url.pathname.endsWith("/settings/feed")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        if (body.columns !== undefined) {
          current().columns = body.columns;
          current().initialized = true;
        }
      }
      return Response.json({
        view: "columns",
        columns: current().columns,
        content_types: ["news"],
        languages: ["en"],
      });
    }
    if (url.pathname.endsWith("/settings/columns")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        writes.push({ owner: session.user!.user_id, ...body });
        if (
          !current().columns.length &&
          (body.mode === "restore_empty" || !current().initialized)
        ) {
          current().columns = body.columns;
          current().initialized = true;
        }
      }
      return Response.json(current());
    }
    if (url.pathname === "/api/v1/topics") {
      const reply = topicsResult;
      topicsResult = null;
      return reply ? reply(init?.signal) : Response.json({ items: topics, next_cursor: null });
    }
    throw new Error(`Unexpected starter request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    writes,
    requests,
    failTopics() {
      topicsResult = async () => Response.json({}, { status: 503 });
    },
    deferTopics() {
      let resolve!: (response: Response) => void;
      const pending = new Promise<Response>((done) => {
        resolve = done;
      });
      topicsResult = () => pending;
      return async () => {
        await act(async () => resolve(Response.json({ items: topics, next_cursor: null })));
      };
    },
  };
}

function Harness({ queue }: { queue: ColumnRequestQueue }) {
  const preferences = useFeedPreferences();
  return (
    <>
      <output aria-label="Configured columns">{JSON.stringify(preferences.columns)}</output>
      <button onClick={() => void preferences.setColumns([chosen])}>Keep my selection</button>
      {!preferences.loading && !preferences.columns.length && <StarterColumns queue={queue} />}
    </>
  );
}
function App({ queue }: { queue: ColumnRequestQueue }) {
  return (
    <FeedPreferencesProvider>
      <Harness queue={queue} />
    </FeedPreferencesProvider>
  );
}
const configured = (): ReaderColumn[] =>
  JSON.parse(screen.getByLabelText("Configured columns").textContent!);

beforeEach(() => {
  session.user = null;
  session.loading = false;
  session.sessionRevision = 1;
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each(["guest", "account"])(
  "automatically creates Latest and two popular topics for a fresh %s workspace",
  async (owner) => {
    session.user = owner === "account" ? account : null;
    const api = transport();
    render(<App queue={createColumnRequestQueue()} />);
    await waitFor(() => expect(configured()).toHaveLength(owner === "account" ? 4 : 3));
    expect(configured().map(({ feed }) => feed)).toEqual([
      { kind: "latest" },
      { kind: "topic", id: topics[0].id },
      { kind: "topic", id: topics[1].id },
      ...(owner === "account" ? [{ kind: "recommended" }] : []),
    ]);
    expect(new Set(configured().map(({ id }) => id)).size).toBe(configured().length);
    const discovery = api.requests.filter(({ url }) => url.pathname === "/api/v1/topics");
    expect(discovery).toHaveLength(1);
    expect(discovery[0].url.searchParams.get("limit")).toBe("2");
    expect(discovery[0].url.searchParams.get("sort")).toBe("articles");
    if (owner === "account") {
      expect(api.writes).toHaveLength(1);
      expect(api.writes[0]).toMatchObject({ owner: account.user_id, mode: "initialize" });
      expect(localStorage.getItem(guestReaderPreferencesKey)).toBeNull();
    } else {
      expect(api.writes).toHaveLength(0);
      expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!).columns_initialized).toBe(
        true,
      );
    }
  },
);

it.each(["guest", "account"])(
  "preserves an initialized empty %s workspace and restores starters only when requested",
  async (owner) => {
    session.user = owner === "account" ? account : null;
    if (owner === "guest")
      localStorage.setItem(
        guestReaderPreferencesKey,
        JSON.stringify({ version: 1, view: "columns", columns: [] }),
      );
    const api = transport(true);
    render(<App queue={createColumnRequestQueue()} />);
    const restore = await screen.findByRole("button", { name: "Use starter columns" });
    expect(configured()).toEqual([]);
    expect(api.requests.filter(({ url }) => url.pathname === "/api/v1/topics")).toHaveLength(0);
    expect(api.writes).toHaveLength(0);
    fireEvent.click(restore);
    await waitFor(() => expect(configured()).toHaveLength(owner === "account" ? 4 : 3));
    if (owner === "account") expect(api.writes[0].mode).toBe("restore_empty");
  },
);

it("offers a retry after catalog failure without consuming guest first use", async () => {
  const api = transport();
  api.failTopics();
  render(<App queue={createColumnRequestQueue()} />);
  const retry = await screen.findByRole("button", { name: "Try again" });
  expect(configured()).toEqual([]);
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBeNull();
  fireEvent.click(retry);
  await waitFor(() => expect(configured()).toHaveLength(3));
  expect(api.requests.filter(({ url }) => url.pathname === "/api/v1/topics")).toHaveLength(2);
});

it("aborts topic discovery when unmounted and never installs its late result", async () => {
  const api = transport();
  const finish = api.deferTopics();
  const app = render(<App queue={createColumnRequestQueue()} />);
  await waitFor(() =>
    expect(api.requests.filter(({ url }) => url.pathname === "/api/v1/topics")).toHaveLength(1),
  );
  const request = api.requests.find(({ url }) => url.pathname === "/api/v1/topics")!;
  app.unmount();
  expect(request.signal?.aborted).toBe(true);
  await finish();
  expect(localStorage.getItem(guestReaderPreferencesKey)).toBeNull();
  expect(api.writes).toHaveLength(0);
});

it.each(["guest", "account"])(
  "keeps a manually chosen %s workspace when delayed starter discovery finishes",
  async (owner) => {
    session.user = owner === "account" ? account : null;
    const api = transport();
    const finish = api.deferTopics();
    render(<App queue={createColumnRequestQueue()} />);
    await waitFor(() =>
      expect(api.requests.filter(({ url }) => url.pathname === "/api/v1/topics")).toHaveLength(1),
    );
    const request = api.requests.find(({ url }) => url.pathname === "/api/v1/topics")!;
    fireEvent.click(screen.getByRole("button", { name: "Keep my selection" }));
    await waitFor(() => expect(configured()).toEqual([chosen]));
    expect(request.signal?.aborted).toBe(true);
    await finish();
    expect(configured()).toEqual([chosen]);
    expect(api.writes).toHaveLength(0);
  },
);

it("cancels an old account's discovery and initializes only the current owner's workspace", async () => {
  session.user = account;
  const api = transport();
  const finish = api.deferTopics();
  const queue = createColumnRequestQueue();
  const app = render(<App queue={queue} />);
  await waitFor(() =>
    expect(api.requests.filter(({ url }) => url.pathname === "/api/v1/topics")).toHaveLength(1),
  );
  const old = api.requests.find(({ url }) => url.pathname === "/api/v1/topics")!;
  session.user = { ...account, user_id: "reader-b", csrf_token: "csrf-b" };
  session.sessionRevision++;
  app.rerender(<App queue={queue} />);
  expect(old.signal?.aborted).toBe(true);
  await waitFor(() => expect(configured()).toHaveLength(4));
  await finish();
  expect(api.writes).toHaveLength(1);
  expect(api.writes[0].owner).toBe("reader-b");
});
