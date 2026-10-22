// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { ColumnsWorkspace } from "@/components/columns-workspace";
import Link from "@/components/reader-link";
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
  profile: null,
  refreshProfile: vi.fn(),
  notify: vi.fn(),
  push: vi.fn(),
}));
vi.mock("@/components/user-account", () => ({
  useUser: () => state,
  AccountGate: ({
    children,
    title,
    description,
  }: {
    children: ReactNode;
    title: string;
    description: string;
  }) =>
    state.user ? (
      children
    ) : (
      <section>
        <h2>{title}</h2>
        <p>{description}</p>
        <Link href="/login">Sign in</Link>
      </section>
    ),
}));
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
  const personal = new Map<string, ReaderColumn[]>();
  const writes: { owner: string; value: FeedDisplay; init?: RequestInit }[] = [];
  let nextWrite: ((value: FeedDisplay) => Promise<Response>) | null = null;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href);
    if (url.pathname === "/api/v1/feed" || url.pathname === "/api/v1/user/feed")
      return Response.json({
        items: [],
        next_cursor: null,
        status: "ready",
        has_interests: true,
        reasons: {},
        generation: "generation-a",
      });
    if (url.pathname === "/api/v1/user/engagement")
      return Response.json(
        url.searchParams.getAll("article_id").map((article_id) => ({
          article_id,
          likes: 0,
          opens: 0,
          liked: false,
          bookmarked: false,
        })),
      );
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
    if (url.pathname === "/api/v1/user/settings/columns") {
      const owner = state.user?.user_id ?? "guest";
      expect(url.searchParams.get("workspace")).toBe("my_feed");
      if (init?.method === "PUT") {
        const { columns } = JSON.parse(String(init.body));
        const value = preferences(columns);
        writes.push({ owner, value, init });
        if (nextWrite) {
          const reply = nextWrite;
          nextWrite = null;
          return reply(value);
        }
        personal.set(owner, columns);
      }
      return Response.json({
        initialized: true,
        columns: personal.get(owner) ?? accounts.get(owner)?.columns ?? [],
      });
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
      return async (value: FeedDisplay, status = 200) => {
        await act(async () =>
          finish(Response.json({ initialized: true, columns: value.columns }, { status })),
        );
      };
    },
  };
}

function Workspace() {
  return (
    <FeedPreferencesProvider>
      {/* Direct mounts keep legacy guest persistence covered; route eligibility has its own suite. */}
      <ColumnsWorkspace workspace={state.user ? "my_feed" : "latest"} />
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

async function chooseColumnAction(name: string, action: string) {
  fireEvent.keyDown(screen.getByRole("button", { name: `Options for ${name} column` }), {
    key: "Enter",
  });
  const menu = await screen.findByRole("menu");
  const item = within(menu).getByRole("menuitem", { name: action });
  expect(item.getAttribute("aria-label")).toContain(item.textContent?.trim());
  fireEvent.click(item);
  if (action.startsWith("Change feed ")) await screen.findByRole("dialog", { name: "Edit column" });
}

function columnOrder() {
  return [...document.querySelectorAll("[data-column-id]")].map((column) =>
    column.getAttribute("data-column-id"),
  );
}

async function beginColumnDrag(name: string) {
  const keyboard = userEvent.setup();
  const handle = screen.getByRole("button", { name: `Reorder ${name} column` });
  handle.focus();
  await keyboard.keyboard("[Space]");
  await waitFor(() => expect(handle.getAttribute("aria-pressed")).toBe("true"));
  return { keyboard, handle };
}

async function dragColumn(name: string, direction: "left" | "right") {
  const { keyboard, handle } = await beginColumnDrag(name);
  await keyboard.keyboard(direction === "left" ? "[ArrowLeft]" : "[ArrowRight]");
  await keyboard.keyboard("[Space]");
  await waitFor(() => expect(handle.getAttribute("aria-pressed")).not.toBe("true"));
  return handle;
}

beforeEach(() => {
  state.user = null;
  state.loading = false;
  state.sessionRevision = 1;
  state.notify.mockClear();
  state.push.mockClear();
  const bounds = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const column = this.closest<HTMLElement>("[data-column-id]");
    if (!column) return bounds.call(this);
    const index = [...document.querySelectorAll("[data-column-id]")].indexOf(column);
    return new DOMRect(24 + index * 336, 120, 320, 640);
  });
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
  expect(screen.getByRole("heading", { name: "Latest" })).toBeTruthy();
  for (const kind of ["latest", "recommended"]) {
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    expect(screen.getByRole("dialog", { name: "Add column" })).toBeTruthy();
    await editFeed(kind);
    await saveEditor();
  }
  const saved = storedColumns();
  expect(saved.map(({ feed }) => feed)).toEqual([{ kind: "latest" }, { kind: "recommended" }]);
  expect(new Set(saved.map(({ id }) => id)).size).toBe(2);
  for (const column of saved)
    expect(column.id).toMatch(/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  app.unmount();
  render(<Workspace />);
  await screen.findByRole("button", { name: "Options for Latest column" });
  expect(
    [...document.querySelectorAll("[data-column-id]")].map((node) =>
      node.getAttribute("data-column-id"),
    ),
  ).toEqual(saved.map(({ id }) => id));
  expect(api.writes).toHaveLength(0);
  expect(
    api.fetcher.mock.calls.every(
      ([input]) => new URL(String(input), window.location.href).pathname === "/api/v1/feed",
    ),
  ).toBe(true);
});

it("opens and saves Add column when randomUUID is unavailable on the current origin", async () => {
  const secureRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
  const getRandomValues = vi.fn((bytes: Uint8Array) => secureRandomValues(bytes));
  vi.stubGlobal("crypto", { randomUUID: undefined, getRandomValues });
  transport();
  await mount([latest]);
  fireEvent.click(screen.getByRole("button", { name: "Add column" }));
  expect(screen.getByRole("dialog", { name: "Add column" })).toBeTruthy();
  await editFeed("recommended");
  await saveEditor();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(storedColumns()[0]).toEqual(latest);
  expect(storedColumns()[1].feed).toEqual({ kind: "recommended" });
  expect(storedColumns()[1].id).toMatch(
    /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/,
  );
  expect(storedColumns()[1].id).not.toBe(latest.id);
  expect(getRandomValues).toHaveBeenCalled();
});

it.each(["guest", "account"])(
  "marks existing built-in and catalog feeds Added before creating a %s column",
  async (owner) => {
    state.user = owner === "account" ? account : null;
    const source: ReaderColumn = {
      id: uuid(3),
      feed: { kind: "source", id: catalog.source.id.toUpperCase() },
    };
    const existing = [latest, recommended, source];
    const api = transport(existing);
    await mount(existing);
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    const dialog = screen.getByRole("dialog", { name: "Add column" });
    const kind = within(dialog).getByRole("combobox", { name: "Feed type" }) as HTMLSelectElement;
    expect(kind.value).toBe("topic");
    for (const label of ["Latest · Added", "Recommended · Added"])
      expect(
        (within(dialog).getByRole("option", { name: label }) as HTMLOptionElement).disabled,
      ).toBe(true);
    fireEvent.change(kind, { target: { value: "source" } });
    const choices = within(dialog).getByRole("group", { name: "Choose a source" });
    const choice = await within(choices).findByRole("button", {
      name: `${catalog.source.name} Added`,
    });
    expect((choice as HTMLButtonElement).disabled).toBe(true);
    expect(choice.textContent).toBe(`${catalog.source.name} Added`);
    expect(choice.getAttribute("aria-label")).toBe(`${catalog.source.name} Added`);
    expect(
      (within(dialog).getByRole("button", { name: "Save column" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(choice);
    expect(api.writes).toHaveLength(0);
    expect(document.querySelectorAll("[data-column-id]")).toHaveLength(3);
  },
);

it.each(["guest", "account"])(
  "allows editing its own feed but blocks selecting another existing %s column",
  async (owner) => {
    state.user = owner === "account" ? account : null;
    const api = transport([latest, recommended]);
    await mount([latest, recommended]);
    await chooseColumnAction("Latest", "Change feed for Latest column");
    const dialog = screen.getByRole("dialog", { name: "Edit column" });
    expect(
      (within(dialog).getByRole("option", { name: "Latest" }) as HTMLOptionElement).disabled,
    ).toBe(false);
    expect(
      (within(dialog).getByRole("option", { name: "Recommended · Added" }) as HTMLOptionElement)
        .disabled,
    ).toBe(true);
    // A stale native-select event must still hit the same save guard as a duplicate catalog result.
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Feed type" }), {
      target: { value: "recommended" },
    });
    expect(within(dialog).getByRole("alert").textContent).toBe(
      "This feed is already in your workspace.",
    );
    const save = within(dialog).getByRole("button", { name: "Save column" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(api.writes).toHaveLength(0);
    expect(document.querySelectorAll("[data-column-id]")).toHaveLength(2);
    expect(screen.queryByRole("dialog")).toBe(dialog);
  },
);

it("edits, reorders, and removes columns without changing their identities", async () => {
  transport();
  const app = await mount([latest, recommended]);
  for (const name of ["Latest", "Recommended"]) {
    const header = screen.getByRole("heading", { name, level: 2 }).parentElement!.parentElement!;
    expect(
      within(header)
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual([`Reorder ${name} column`, `Options for ${name} column`]);
  }
  expect(screen.queryByRole("button", { name: /^Remove .+ column$/ })).toBeNull();
  await chooseColumnAction("Latest", "Change feed for Latest column");
  expect(screen.getByRole("dialog", { name: "Edit column" })).toBeTruthy();
  await editFeed("source", catalog.source.name);
  await saveEditor();
  expect(storedColumns()).toEqual([
    { ...latest, feed: { kind: "source", id: catalog.source.id } },
    recommended,
  ]);
  await screen.findByRole("button", { name: "Options for Rust Blog column" });
  const first = document.querySelector(`[data-column-id="${latest.id}"]`)!;
  await dragColumn("Rust Blog", "right");
  await waitFor(() =>
    expect(storedColumns().map(({ id }) => id)).toEqual([recommended.id, latest.id]),
  );
  expect(document.querySelector(`[data-column-id="${latest.id}"]`)).toBe(first);
  await chooseColumnAction("Rust Blog", "Remove column: Rust Blog");
  await waitFor(() => expect(storedColumns()).toEqual([recommended]));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      document.querySelector(`[data-column-id="${recommended.id}"]`),
    ),
  );
  await chooseColumnAction("Recommended", "Remove column: Recommended");
  await waitFor(() => expect(storedColumns()).toEqual([]));
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add column" })),
  );
  app.unmount();
  render(<Workspace />);
  await screen.findByRole("heading", { name: "Build your reading workspace" });
  expect(document.querySelectorAll("[data-column-id]")).toHaveLength(0);
  expect(storedColumns()).toEqual([]);
});

it("saves one keyboard drop and keeps its focused stable handle while the account write is pending", async () => {
  state.user = account;
  const api = transport([latest, recommended]);
  await mount();
  const original = document.querySelector(`[data-column-id="${latest.id}"]`);
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for Latest column" }), {
    key: "Enter",
  });
  const menu = await screen.findByRole("menu");
  expect(within(menu).queryByRole("menuitem", { name: /^Move (left|right)/ })).toBeNull();
  fireEvent.keyDown(menu, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  const complete = api.delayNextWrite();
  const { keyboard, handle } = await beginColumnDrag("Latest");
  expect(screen.getByRole("button", { name: "Options for Recommended column" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByRole("button", { name: "Add column" })).toHaveProperty("disabled", true);
  expect(api.writes).toHaveLength(0);
  await keyboard.keyboard("[ArrowRight]");
  expect(api.writes).toHaveLength(0);
  await keyboard.keyboard("[Space]");
  await waitFor(() => expect(api.writes).toHaveLength(1));
  expect(api.writes[0].value.columns).toEqual([recommended, latest]);
  expect(columnOrder()).toEqual([recommended.id, latest.id]);
  expect(handle).toHaveProperty("disabled", false);
  expect(handle.getAttribute("aria-disabled")).toBe("true");
  expect(document.activeElement).toBe(handle);
  const other = screen.getByRole("button", { name: "Reorder Recommended column" });
  expect(other.getAttribute("aria-disabled")).toBe("true");
  fireEvent.keyDown(other, { key: " ", code: "Space" });
  fireEvent.keyDown(handle, { key: " ", code: "Space" });
  expect(other.getAttribute("aria-pressed")).not.toBe("true");
  expect(handle.getAttribute("aria-pressed")).not.toBe("true");
  expect(api.writes).toHaveLength(1);
  await complete(preferences([recommended, latest]));
  await waitFor(() => expect(handle.getAttribute("aria-disabled")).not.toBe("true"));
  expect(columnOrder()).toEqual([recommended.id, latest.id]);
  expect(document.querySelector(`[data-column-id="${latest.id}"]`)).toBe(original);
  expect(document.activeElement).toBe(handle);
  expect(api.writes).toHaveLength(1);
});

it("moves a column to a selected position using clicks and saves once with stable handle focus", async () => {
  state.user = account;
  const api = transport([latest, recommended]);
  await mount();
  // jsdom has no submenu hit geometry; exercise pointer activation without synthetic hover travel.
  const pointer = userEvent.setup({ skipHover: true });
  const handle = screen.getByRole("button", { name: "Reorder Latest column" });
  const original = document.querySelector(`[data-column-id="${latest.id}"]`);
  await pointer.click(screen.getByRole("button", { name: "Options for Latest column" }));
  const menu = await screen.findByRole("menu");
  expect(within(menu).queryByRole("menuitem", { name: /^Move (left|right)/ })).toBeNull();
  await pointer.click(within(menu).getByRole("menuitem", { name: "Move to position" }));
  const current = await screen.findByRole("menuitem", { name: "Position 1 (current)" });
  expect(current.getAttribute("aria-disabled")).toBe("true");
  expect(api.writes).toHaveLength(0);
  const complete = api.delayNextWrite();
  await pointer.click(screen.getByRole("menuitem", { name: "Position 2" }));
  await waitFor(() => expect(api.writes).toHaveLength(1));
  expect(api.writes[0].value.columns).toEqual([recommended, latest]);
  expect(columnOrder()).toEqual([recommended.id, latest.id]);
  await complete(preferences([recommended, latest]));
  await waitFor(() => expect(handle.getAttribute("aria-disabled")).not.toBe("true"));
  await waitFor(() => expect(document.activeElement).toBe(handle));
  expect(document.querySelector(`[data-column-id="${latest.id}"]`)).toBe(original);
  expect(api.writes).toHaveLength(1);
});

it.each(["unchanged", "cancelled"] as const)(
  "does not save an %s keyboard drag and restores handle focus",
  async (outcome) => {
    state.user = account;
    const api = transport([latest, recommended]);
    await mount();
    const { keyboard, handle } = await beginColumnDrag("Latest");
    if (outcome === "cancelled") await keyboard.keyboard("[ArrowRight]");
    await keyboard.keyboard(outcome === "cancelled" ? "[Escape]" : "[Space]");
    await waitFor(() => expect(handle.getAttribute("aria-pressed")).not.toBe("true"));
    expect(columnOrder()).toEqual([latest.id, recommended.id]);
    await waitFor(() => expect(document.activeElement).toBe(handle));
    expect(api.writes).toHaveLength(0);
    expect(state.notify).not.toHaveBeenCalled();
  },
);

it("rolls a rejected keyboard drop back to the saved order and allows a focused retry", async () => {
  state.user = account;
  const api = transport([latest, recommended]);
  await mount();
  const original = document.querySelector(`[data-column-id="${latest.id}"]`);
  const complete = api.delayNextWrite();
  const handle = await dragColumn("Latest", "right");
  await waitFor(() => expect(api.writes).toHaveLength(1));
  expect(columnOrder()).toEqual([recommended.id, latest.id]);
  await complete(preferences([latest, recommended]), 503);
  await screen.findByRole("alert");
  await waitFor(() => expect(columnOrder()).toEqual([latest.id, recommended.id]));
  expect(document.querySelector(`[data-column-id="${latest.id}"]`)).toBe(original);
  expect(document.activeElement).toBe(handle);
  expect(handle.getAttribute("aria-disabled")).not.toBe("true");
  await dragColumn("Latest", "right");
  await waitFor(() => expect(columnOrder()).toEqual([recommended.id, latest.id]));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(api.writes).toHaveLength(2);
  expect(api.writes[1].value.columns).toEqual([recommended, latest]);
  expect(document.activeElement).toBe(handle);
});

it("retains a column when its menu removal cannot be saved and allows retry", async () => {
  state.user = account;
  const api = transport([latest, recommended]);
  await mount();
  api.rejectNextWrite();
  await chooseColumnAction("Latest", "Remove column: Latest");
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Options for Latest column" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Options for Recommended column" })).toBeTruthy();
  expect(state.notify).not.toHaveBeenCalledWith("Column removed.");
  await chooseColumnAction("Latest", "Remove column: Latest");
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Options for Latest column" })).toBeNull(),
  );
  expect(api.writes.at(-1)?.value.columns).toEqual([recommended]);
  expect(state.notify).toHaveBeenCalledWith("Column removed.");
});

it("focuses Add column after the last account removal finishes saving", async () => {
  state.user = account;
  const api = transport([latest]);
  await mount();
  const complete = api.delayNextWrite();
  await chooseColumnAction("Latest", "Remove column: Latest");
  await waitFor(() => expect(api.writes).toHaveLength(1));
  expect((screen.getByRole("button", { name: "Add column" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(screen.getByRole("button", { name: "Options for Latest column" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByRole("button", { name: "Reorder Latest column" }).getAttribute("aria-disabled"),
  ).toBe("true");
  expect(api.writes).toHaveLength(1);
  await complete(preferences([]));
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add column" })),
  );
  expect(document.querySelectorAll("[data-column-id]")).toHaveLength(0);
});

it.each(["source", "topic", "tag"] as const)(
  "selects a %s by UUID and restores its named header without a feed-type subtitle",
  async (kind) => {
    const api = transport();
    const app = await mount();
    fireEvent.click(screen.getByRole("button", { name: "Add column" }));
    const picker = within(screen.getByRole("dialog", { name: "Add column" }));
    for (const label of ["Source", "Topic", "Tag"])
      expect(picker.getByRole("option", { name: label }).getAttribute("value")).toBe(
        label.toLowerCase(),
      );
    await editFeed(kind, catalog[kind].name);
    await saveEditor();
    const saved = storedColumns();
    expect(saved).toHaveLength(1);
    expect(saved[0].feed).toEqual({ kind, id: catalog[kind].id });
    app.unmount();
    render(<Workspace />);
    await screen.findByRole("button", { name: `Options for ${catalog[kind].name} column` });
    const heading = screen.getByRole("heading", { name: catalog[kind].name, level: 2 });
    const header = within(heading.parentElement!.parentElement!);
    expect(header.queryByText(/^(Source|Topic|Tag)$/)).toBeNull();
    expect(
      header.getByRole("button", { name: `Reorder ${catalog[kind].name} column` }),
    ).toBeTruthy();
    expect(
      header.getByRole("button", { name: `Options for ${catalog[kind].name} column` }),
    ).toBeTruthy();
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
  await screen.findByRole("button", { name: "Options for Unavailable topic column" });
  expect(storedColumns()).toEqual([missing]);
  expect(screen.queryByText(uuid(999))).toBeNull();
  await chooseColumnAction("Unavailable topic", "Change feed for Unavailable topic column");
  await editFeed("latest");
  await saveEditor();
  expect(storedColumns()).toEqual([{ id: missing.id, feed: { kind: "latest" } }]);
});

it("limits the workspace to twelve columns and makes space after removal", async () => {
  const columns: ReaderColumn[] = Array.from({ length: maxReaderColumns }, (_, index) => ({
    id: uuid(index + 1),
    feed: index === 0 ? { kind: "latest" } : { kind: "topic", id: uuid(1000 + index) },
  }));
  transport();
  await mount(columns);
  expect((screen.getByRole("button", { name: "Add column" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  await chooseColumnAction("Latest", "Remove column: Latest");
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
  expect(
    screen
      .getByRole("button", { name: "Reorder Recommended column" })
      .getAttribute("aria-disabled"),
  ).toBe("true");
  expect(storedColumns()).toEqual([recommended]);
  expect(api.fetcher).not.toHaveBeenCalled();
});

it("rolls back a rejected account edit, keeps the editor open, and permits retry", async () => {
  state.user = account;
  const api = transport([latest]);
  await mount();
  api.rejectNextWrite();
  await chooseColumnAction("Latest", "Change feed for Latest column");
  await editFeed("recommended");
  await saveEditor();
  expect(screen.getByRole("dialog", { name: "Edit column" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Options for Latest column" })).toBeTruthy();
  expect(within(screen.getByRole("dialog")).getByRole("alert").textContent).toContain(
    "Couldn’t save",
  );
  expect(state.notify.mock.calls.filter(([, variant]) => variant !== "error")).toHaveLength(0);
  await saveEditor();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Options for Recommended column" })).toBeTruthy();
  expect(api.writes.at(-1)?.value.columns).toEqual([{ ...latest, feed: { kind: "recommended" } }]);
  expect(api.writes.at(-1)?.init?.headers).toMatchObject({ "X-CSRF-Token": "csrf-a" });
});

it("cancels an editor without mutating the saved workspace", async () => {
  transport();
  await mount([latest]);
  await chooseColumnAction("Latest", "Change feed for Latest column");
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
  await chooseColumnAction("Latest", "Change feed for Latest column");
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
    false,
  );
  expect(screen.getByRole("button", { name: "Saving…" }).getAttribute("aria-disabled")).toBe(
    "true",
  );
  expect(screen.getByRole("button", { name: "Saving…" }).getAttribute("aria-busy")).toBe("true");
  expect(state.notify).not.toHaveBeenCalled();
  await complete(preferences([{ ...latest, feed: { kind: "recommended" } }]));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Options for Recommended column" })).toBeTruthy();
  expect(api.writes).toHaveLength(1);
});

it("retains keyboard focus through a rejected save and permits one focused retry without overlapping submits", async () => {
  state.user = account;
  const api = transport([latest]);
  await mount();
  const complete = api.delayNextWrite();
  await chooseColumnAction("Latest", "Change feed for Latest column");
  await editFeed("recommended");
  const dialog = screen.getByRole("dialog", { name: "Edit column" });
  const form = dialog.querySelector("form")!;
  const selection = within(dialog).getByRole("combobox", { name: "Feed type" });
  selection.focus();
  expect(document.activeElement).toBe(selection);
  fireEvent.submit(form);
  await waitFor(() => expect(api.writes).toHaveLength(1));
  const save = within(dialog).getByRole("button", { name: "Saving…" });
  expect(save).toHaveProperty("disabled", false);
  expect(save.getAttribute("aria-disabled")).toBe("true");
  expect(save.getAttribute("aria-busy")).toBe("true");
  expect(document.activeElement).toBe(save);
  expect(dialog.contains(document.activeElement)).toBe(true);
  fireEvent.click(save);
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(api.writes).toHaveLength(1);
  await complete(preferences([latest]), 503);
  expect(screen.getByRole("dialog", { name: "Edit column" })).toBe(dialog);
  expect(within(dialog).getByRole("alert").textContent).toContain("Couldn’t save");
  expect(within(dialog).getByRole("button", { name: "Save column" })).toBe(save);
  expect(save).toHaveProperty("disabled", false);
  expect(save.getAttribute("aria-disabled")).not.toBe("true");
  expect(save.getAttribute("aria-busy")).not.toBe("true");
  expect(document.activeElement).toBe(save);
  expect(selection).toHaveProperty("disabled", false);
  fireEvent.click(save);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("button", { name: "Options for Recommended column" })).toBeTruthy();
  expect(api.writes).toHaveLength(2);
  expect(api.writes[0].value.columns).toEqual([{ ...latest, feed: { kind: "recommended" } }]);
  expect(api.writes[1].value.columns).toEqual(api.writes[0].value.columns);
});

it("discards an old account's open editor when the account changes", async () => {
  state.user = account;
  const api = transport([latest]);
  api.accounts.set("reader-b", preferences([recommended]));
  const app = await mount();
  await chooseColumnAction("Latest", "Change feed for Latest column");
  await editFeed("topic", catalog.topic.name);
  state.user = { ...account, user_id: "reader-b", csrf_token: "csrf-b" };
  state.sessionRevision++;
  app.rerender(<Workspace />);
  await screen.findByRole("button", { name: "Options for Recommended column" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Options for Latest column" })).toBeNull();
  expect(api.writes).toHaveLength(0);
});

it("browses an empty configured feed inside Columns without changing the saved layout", async () => {
  const api = transport();
  await mount([latest]);
  const feed = await screen.findByRole("region", { name: "Articles in Latest column" });
  expect(await within(feed).findByText("No articles yet.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open Latest feed" })).toBeNull();
  expect(state.push).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem(guestReaderPreferencesKey)!)).toEqual({
    version: 1,
    view: "columns",
    columns: [latest],
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

it("keeps the column board labelled and keyboard focusable without a workspace toolbar", async () => {
  transport();
  await mount([latest, recommended]);
  expect(screen.queryByRole("combobox", { name: "Go to column" })).toBeNull();
  expect(screen.queryByText("2 of 12 columns")).toBeNull();
  const board = screen.getByRole("region", { name: "Column feeds" });
  expect(board.tabIndex).toBe(0);
  board.focus();
  expect(document.activeElement).toBe(board);
  expect(board.querySelectorAll("[data-column-id]")).toHaveLength(2);
  expect(storedColumns()).toEqual([latest, recommended]);
  expect(state.push).not.toHaveBeenCalled();
});
