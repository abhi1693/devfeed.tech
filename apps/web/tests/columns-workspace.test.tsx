// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ColumnsWorkspace } from "@/components/columns-workspace";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import {
  guestReaderPreferencesKey,
  maxReaderColumns,
  type ReaderColumn,
} from "@/lib/reader-preferences";
import type { FeedDisplay, UserIdentity } from "@/lib/user";

const state = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
  notify: vi.fn(),
  push: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/settings-toast", () => ({ useSettingsToast: () => state.notify }));
vi.mock("@/lib/reader-navigation", () => ({ useReaderRouter: () => ({ push: state.push }) }));

const uuid = (index: number) => `aaaaaaaa-aaaa-4aaa-8aaa-${index.toString(16).padStart(12, "0")}`;
const account: UserIdentity = {
  user_id: "reader-a",
  csrf_token: "csrf-a",
  name: "Reader A",
  email: null,
  expires_at: 4102444800,
};
const latest: ReaderColumn = { id: uuid(1), feed: { kind: "latest" } };
const recommended: ReaderColumn = { id: uuid(2), feed: { kind: "recommended" } };
const catalog = {
  source: {
    id: uuid(101),
    name: "Rust Blog",
    slug: "rust-blog",
    website_url: "https://rust.example.test",
    logo_url: null,
    description: null,
  },
  topic: {
    id: uuid(102),
    name: "Rust",
    slug: "rust",
    kind: "technology",
    website_url: null,
    logo_url: null,
    description: null,
    ai_description: null,
  },
  tag: { id: uuid(103), name: "async", slug: "async" },
};
const preferences = (columns: ReaderColumn[]): FeedDisplay => ({
  view: "columns",
  columns,
  languages: ["en"],
  content_types: ["news"],
});

function storedColumns(): ReaderColumn[] {
  return JSON.parse(localStorage.getItem(guestReaderPreferencesKey) ?? '{"columns":[]}').columns;
}

function transport(initial: ReaderColumn[] = []) {
  const accounts = new Map([[account.user_id, preferences(initial)]]);
  const writes: { owner: string; value: FeedDisplay; init?: RequestInit }[] = [];
  let nextWrite: ((value: FeedDisplay) => Promise<Response>) | null = null;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href);
    if (url.pathname === "/api/v1/user/settings/feed") {
      const owner = state.user?.user_id ?? "guest";
      if (init?.method === "PUT") {
        const value = JSON.parse(String(init.body)) as FeedDisplay;
        writes.push({ owner, value, init });
        if (nextWrite) {
          const reply = nextWrite;
          nextWrite = null;
          return reply(value);
        }
        accounts.set(owner, value);
      }
      return Response.json(accounts.get(owner) ?? preferences([]));
    }
    const match = url.pathname.match(/^\/api\/v1\/(sources|topics|tags)(?:\/([^/]+))?$/);
    if (match) {
      const kind = { sources: "source", topics: "topic", tags: "tag" }[
        match[1]
      ] as keyof typeof catalog;
      const entity = catalog[kind];
      if (match[2])
        return match[2] === entity.id
          ? Response.json(entity)
          : Response.json({ detail: "Not found" }, { status: 404 });
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      return Response.json({
        items: entity.name.toLowerCase().includes(query) ? [entity] : [],
        next_cursor: null,
      });
    }
    throw new Error(`Unexpected workspace request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    fetcher,
    writes,
    accounts,
    rejectNextWrite() {
      nextWrite = async () => Response.json({ detail: "Unavailable" }, { status: 503 });
    },
    delayNextWrite() {
      let finish!: (response: Response) => void;
      nextWrite = () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return async (value: FeedDisplay) => {
        await act(async () => finish(Response.json(value)));
      };
    },
  };
}

function Workspace() {
  return (
    <FeedPreferencesProvider>
      <ColumnsWorkspace />
    </FeedPreferencesProvider>
  );
}

async function mount(columns: ReaderColumn[] = []) {
  if (!state.user)
    localStorage.setItem(
      guestReaderPreferencesKey,
      JSON.stringify({ version: 1, view: "columns", columns }),
    );
  const app = render(<Workspace />);
  const region = await screen.findByRole("region", { name: "Columns workspace" });
  await waitFor(() => expect(region.getAttribute("aria-busy")).toBe("false"));
  return app;
}

async function editFeed(kind: string, name?: string) {
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Feed type" }), {
    target: { value: kind },
  });
  if (name) {
    fireEvent.change(within(dialog).getByRole("searchbox", { name: `Find a ${kind}` }), {
      target: { value: name },
    });
    fireEvent.click(await within(dialog).findByRole("button", { name }));
  }
}

async function saveEditor() {
  await act(async () =>
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Save column" }),
    ),
  );
}

beforeEach(() => {
  state.user = null;
  state.loading = false;
  state.sessionRevision = 1;
  state.notify.mockClear();
  state.push.mockClear();
  localStorage.clear();
  window.history.replaceState(null, "", "/latest?tag=rust");
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("adds independent columns with stable UUIDs and restores the saved workspace", async () => {
  const api = transport();
  const app = await mount();
  expect(screen.getByRole("heading", { name: "Your columns" })).toBeTruthy();
  for (let index = 0; index < 2; index++) {
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    expect(screen.getByRole("dialog", { name: "Add column" })).toBeTruthy();
    await editFeed("latest");
    await saveEditor();
  }
  const saved = storedColumns();
  expect(saved.map(({ feed }) => feed)).toEqual([{ kind: "latest" }, { kind: "latest" }]);
  expect(new Set(saved.map(({ id }) => id)).size).toBe(2);
  for (const column of saved)
    expect(column.id).toMatch(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  app.unmount();
  render(<Workspace />);
  await screen.findAllByRole("button", { name: "Edit Latest column" });
  expect(
    [...document.querySelectorAll("[data-column-id]")].map((node) =>
      node.getAttribute("data-column-id"),
    ),
  ).toEqual(saved.map(({ id }) => id));
  expect(api.fetcher).not.toHaveBeenCalled();
});

it("edits, reorders, and removes columns without changing their identities", async () => {
  transport();
  await mount([latest, recommended]);
  fireEvent.click(screen.getByRole("button", { name: "Edit Latest column" }));
  expect(screen.getByRole("dialog", { name: "Edit column" })).toBeTruthy();
  await editFeed("recommended");
  await saveEditor();
  expect(storedColumns()).toEqual([{ ...latest, feed: { kind: "recommended" } }, recommended]);
  const first = document.querySelector(`[data-column-id="${latest.id}"]`)!;
  fireEvent.click(
    within(first as HTMLElement).getByRole("button", { name: "Move Recommended right" }),
  );
  await waitFor(() =>
    expect(storedColumns().map(({ id }) => id)).toEqual([recommended.id, latest.id]),
  );
  fireEvent.click(
    within(first as HTMLElement).getByRole("button", { name: "Remove Recommended column" }),
  );
  await waitFor(() => expect(storedColumns()).toEqual([recommended]));
  fireEvent.click(screen.getByRole("button", { name: "Remove Recommended column" }));
  await waitFor(() => expect(storedColumns()).toEqual([]));
  expect(screen.getByRole("button", { name: "Add column" })).toBeTruthy();
});

it.each(["source", "topic", "tag"] as const)(
  "selects a %s by UUID and resolves its title after reload",
  async (kind) => {
    const api = transport();
    const app = await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    await editFeed(kind, catalog[kind].name);
    await saveEditor();
    const saved = storedColumns();
    expect(saved).toHaveLength(1);
    expect(saved[0].feed).toEqual({ kind, id: catalog[kind].id });
    app.unmount();
    render(<Workspace />);
    await screen.findByRole("button", { name: `Edit ${catalog[kind].name} column` });
    const lookup = api.fetcher.mock.calls
      .map(([input]) => new URL(String(input), window.location.href))
      .find((url) => url.pathname.endsWith(`/${catalog[kind].id}`));
    expect(lookup).toBeDefined();
    if (kind !== "source") expect(lookup!.searchParams.get("by_id")).toBe("true");
    expect(storedColumns()).toEqual(saved);
  },
);

it("keeps unavailable selections editable without deleting or exposing their saved UUIDs", async () => {
  const missing: ReaderColumn = { id: uuid(8), feed: { kind: "topic", id: uuid(999) } };
  transport();
  await mount([missing]);
  await screen.findByRole("button", { name: "Edit Unavailable topic column" });
  expect(storedColumns()).toEqual([missing]);
  expect(screen.queryByText(uuid(999))).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit Unavailable topic column" }));
  await editFeed("latest");
  await saveEditor();
  expect(storedColumns()).toEqual([{ id: missing.id, feed: { kind: "latest" } }]);
});

it("limits the workspace to twelve columns and makes space after removal", async () => {
  const columns: ReaderColumn[] = Array.from({ length: maxReaderColumns }, (_, index) => ({
    id: uuid(index + 1),
    feed: { kind: "latest" },
  }));
  transport();
  await mount(columns);
  expect((screen.getByRole("button", { name: "Add column" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.click(screen.getAllByRole("button", { name: "Remove Latest column" })[0]);
  await waitFor(() =>
    expect((screen.getByRole("button", { name: "Add column" }) as HTMLButtonElement).disabled).toBe(
      false,
    ),
  );
  expect(storedColumns()).toHaveLength(maxReaderColumns - 1);
});

it("retains a guest Recommended column with a sign-in prompt and no account requests", async () => {
  const api = transport();
  await mount([recommended]);
  expect(screen.getByText("Sign in to view recommendations.")).toBeTruthy();
  expect(storedColumns()).toEqual([recommended]);
  expect(api.fetcher).not.toHaveBeenCalled();
});

it("rolls back a rejected account edit, keeps the editor open, and permits retry", async () => {
  state.user = account;
  const api = transport([latest]);
  await mount();
  api.rejectNextWrite();
  fireEvent.click(screen.getByRole("button", { name: "Edit Latest column" }));
  await editFeed("recommended");
  await saveEditor();
  expect(screen.getByRole("dialog", { name: "Edit column" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit Latest column" })).toBeTruthy();
  expect(within(screen.getByRole("dialog")).getByRole("alert").textContent).toContain(
    "Couldn’t save",
  );
  expect(state.notify.mock.calls.filter(([, variant]) => variant !== "error")).toHaveLength(0);
  await saveEditor();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Edit Recommended column" })).toBeTruthy();
  expect(api.writes.at(-1)?.value.columns).toEqual([{ ...latest, feed: { kind: "recommended" } }]);
  expect(api.writes.at(-1)?.init?.headers).toMatchObject({ "X-CSRF-Token": "csrf-a" });
});

it("cancels an editor without mutating the saved workspace", async () => {
  transport();
  await mount([latest]);
  fireEvent.click(screen.getByRole("button", { name: "Edit Latest column" }));
  await editFeed("recommended");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(storedColumns()).toEqual([latest]);
});

it("blocks overlapping edits while a column save is pending", async () => {
  state.user = account;
  const api = transport([latest]);
  await mount();
  const complete = api.delayNextWrite();
  fireEvent.click(screen.getByRole("button", { name: "Edit Latest column" }));
  await editFeed("recommended");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save column" }));
  await waitFor(() => expect(api.writes).toHaveLength(1));
  expect((screen.getByRole("button", { name: "Add column" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(
    (
      within(screen.getByRole("dialog")).getByRole("combobox", {
        name: "Feed type",
      }) as HTMLSelectElement
    ).disabled,
  ).toBe(true);
  expect((screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(state.notify).not.toHaveBeenCalled();
  await complete(preferences([{ ...latest, feed: { kind: "recommended" } }]));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Edit Recommended column" })).toBeTruthy();
  expect(api.writes).toHaveLength(1);
});

it("discards an old account's open editor when the account changes", async () => {
  state.user = account;
  const api = transport([latest]);
  api.accounts.set("reader-b", preferences([recommended]));
  const app = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Edit Latest column" }));
  await editFeed("topic", catalog.topic.name);
  state.user = { ...account, user_id: "reader-b", csrf_token: "csrf-b" };
  state.sessionRevision++;
  app.rerender(<Workspace />);
  await screen.findByRole("button", { name: "Edit Recommended column" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit Latest column" })).toBeNull();
  expect(api.writes).toHaveLength(0);
});

it("opens a configured feed after saving Grid and preserves the workspace for later", async () => {
  const api = transport();
  await mount([latest]);
  fireEvent.click(screen.getByRole("button", { name: "Open Latest feed" }));
  await waitFor(() => expect(state.push).toHaveBeenCalledWith("/latest"));
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!)).toEqual({
    version: 1,
    view: "cards",
    columns: [latest],
  });
  expect(api.fetcher).not.toHaveBeenCalled();
});

it("opens the canonical source feed for this tab when guest layout storage is blocked", async () => {
  const api = transport();
  const source: ReaderColumn = { id: uuid(7), feed: { kind: "source", id: catalog.source.id } };
  await mount([source]);
  const open = await screen.findByRole("button", { name: "Open Rust Blog feed" });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  fireEvent.click(open);
  await waitFor(() => expect(state.push).toHaveBeenCalledWith("/sources/rust-blog"));
  expect(state.notify).toHaveBeenCalledWith(
    "Browser preferences could not be saved. Opened the feed for this tab.",
    "error",
  );
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!)).toEqual({
    version: 1,
    view: "columns",
    columns: [source],
  });
  expect(api.writes).toHaveLength(0);
  expect(api.fetcher.mock.calls.some(([input]) => String(input).includes("/user/"))).toBe(false);
});

it("does not duplicate a temporary column or report success when guest storage is blocked", async () => {
  transport();
  await mount();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  fireEvent.click(screen.getByRole("button", { name: "Add column" }));
  await saveEditor();
  if (screen.queryByRole("dialog")) await saveEditor();
  expect(document.querySelectorAll("[data-column-id]").length).toBeLessThanOrEqual(1);
  expect(storedColumns()).toEqual([]);
  expect(state.notify.mock.calls.filter(([, variant]) => variant !== "error")).toHaveLength(0);
});

it("shows a failed catalog search inside the editor and recovers when retried", async () => {
  const api = transport();
  api.fetcher.mockRejectedValueOnce(new Error("Catalog offline"));
  await mount();
  fireEvent.click(screen.getByRole("button", { name: "Add column" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Feed type" }), {
    target: { value: "source" },
  });
  expect((await within(dialog).findByRole("alert")).textContent).toContain("Couldn’t load sources");
  expect(
    (within(dialog).getByRole("button", { name: "Save column" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
  fireEvent.click(await within(dialog).findByRole("button", { name: catalog.source.name }));
  await saveEditor();
  expect(storedColumns()[0].feed).toEqual({ kind: "source", id: catalog.source.id });
});

it("moves focus to the selected column without changing its order or navigation", async () => {
  transport();
  await mount([latest, recommended]);
  fireEvent.change(screen.getByRole("combobox", { name: "Go to column" }), {
    target: { value: recommended.id },
  });
  expect(document.activeElement?.getAttribute("data-column-id")).toBe(recommended.id);
  expect(storedColumns()).toEqual([latest, recommended]);
  expect(state.push).not.toHaveBeenCalled();
});
