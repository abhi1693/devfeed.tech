// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  guestReaderPreferencesKey,
  guestColumnsInitialized,
  legacyGuestFeedViewKey,
  maxReaderColumns,
  normalizeReaderColumns,
  normalizeReaderPreferences,
  readGuestReaderPreferences,
  readerColumnFeedKey,
  readerPreferencesVersion,
  validateReaderColumns,
  validateReaderPreferences,
  writeGuestReaderPreferences,
  type ReaderColumn,
} from "@/lib/reader-preferences";
import { normalizeFeedView } from "@/lib/reader-layouts";
import { saveFeedSettings } from "@/lib/user-mutations";

const uuid = (index: number) => `abcdef00-0000-0000-0000-${index.toString(16).padStart(12, "0")}`;
const latest = (index: number): ReaderColumn => ({ id: uuid(index), feed: { kind: "latest" } });
const topic = (index: number): ReaderColumn => ({
  id: uuid(index),
  feed: { kind: "topic", id: uuid(100 + index) },
});
const settings = { view: "cards", content_types: ["news"], languages: ["en"] } as const;

function storage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each(["cards", "compact", "stacked", "columns"])(
  "retains saved %s while exposing only implemented layouts",
  (view) => {
    const result = normalizeReaderPreferences({ ...settings, view, columns: [latest(1)] });
    expect(result.view).toBe(view);
    expect(result.columns).toEqual([latest(1)]);
    expect(normalizeFeedView(result.view)).toBe(view);
    expect(validateReaderPreferences(result)).toBe(true);
  },
);

it.each([null, [], {}, "compact", { view: "grid" }, { view: "obsolete", columns: "invalid" }])(
  "recovers safe defaults from malformed or obsolete settings: %j",
  (value) => {
    const result = normalizeReaderPreferences(value);
    expect(result.view).toBe("cards");
    expect(result.columns).toEqual([]);
    expect(result.languages).toEqual(["en"]);
    expect(result.content_types).toHaveLength(6);
  },
);

it("preserves all supported feed kinds and unresolved target UUIDs in saved order", () => {
  const columns: ReaderColumn[] = [
    { id: uuid(1), feed: { kind: "tag", id: uuid(101) } },
    { id: uuid(2), feed: { kind: "source", id: uuid(102) } },
    { id: uuid(3), feed: { kind: "recommended" } },
    { id: uuid(4), feed: { kind: "topic", id: uuid(103) } },
    { id: uuid(5), feed: { kind: "latest" } },
  ];
  const result = normalizeReaderPreferences({ ...settings, columns });
  expect(normalizeReaderColumns(columns)).toEqual(columns);
  expect(validateReaderColumns(columns)).toBe(true);
  expect(result.columns).toEqual(columns);
  expect(result.columns).not.toBe(columns);
  expect(validateReaderPreferences(result)).toBe(true);
});

it.each([
  uuid(1).toUpperCase(),
  uuid(1).replaceAll("-", ""),
  `{${uuid(1)}}`,
  `urn:uuid:${uuid(1)}`,
])("canonicalizes the API's UUID form %s", (identifier) => {
  expect(
    normalizeReaderPreferences({
      columns: [{ id: identifier, feed: { kind: "topic", id: identifier } }],
    }).columns,
  ).toEqual([{ id: uuid(1), feed: { kind: "topic", id: uuid(1) } }]);
});

const invalidColumns = [
  null,
  [],
  { id: "not-a-uuid", feed: { kind: "latest" } },
  { id: `URN:UUID:${uuid(1)}`, feed: { kind: "latest" } },
  { id: `urn:uuid:{${uuid(1)}}`, feed: { kind: "latest" } },
  { id: `{${uuid(1).replaceAll("-", "")}}`, feed: { kind: "latest" } },
  { id: `urn:uuid:${uuid(1).replaceAll("-", "")}`, feed: { kind: "latest" } },
  { id: uuid(1), feed: null },
  { id: uuid(1), feed: { kind: "trending" } },
  { id: uuid(1), feed: { kind: "latest", id: uuid(2) } },
  { id: uuid(1), feed: { kind: "recommended", sort: "oldest" } },
  { id: uuid(1), feed: { kind: "source" } },
  { id: uuid(1), feed: { kind: "topic", id: "slug" } },
  { id: uuid(1), feed: { kind: "tag", id: uuid(2), token: "secret" } },
  { id: uuid(1), feed: { kind: "latest" }, width: 600 },
];

it.each(invalidColumns)(
  "prunes malformed stored columns but rejects them in receipts: %j",
  (column) => {
    const value = { ...settings, columns: [column, latest(9)] };
    expect(normalizeReaderColumns(value.columns)).toEqual([latest(9)]);
    expect(validateReaderColumns(value.columns)).toBe(false);
    expect(normalizeReaderPreferences(value).columns).toEqual([latest(9)]);
    expect(validateReaderPreferences(value)).toBe(false);
  },
);

it("retains the first valid duplicate and bounds normalized workspaces to 12 columns", () => {
  const columns = [
    { id: uuid(1), feed: { kind: "removed" } },
    latest(1),
    { id: uuid(1).toUpperCase(), feed: { kind: "recommended" } },
    ...Array.from({ length: maxReaderColumns + 3 }, (_, index) => topic(index + 2)),
  ];
  expect(normalizeReaderPreferences({ columns }).columns).toEqual([
    latest(1),
    ...Array.from({ length: maxReaderColumns - 1 }, (_, index) => topic(index + 2)),
  ]);
  expect(validateReaderPreferences({ ...settings, columns })).toBe(false);
  expect(validateReaderColumns(columns)).toBe(false);
  expect(
    validateReaderPreferences({ ...settings, columns: columns.slice(3, 3 + maxReaderColumns + 1) }),
  ).toBe(false);
});

it.each(["source", "topic", "tag"] as const)(
  "compares %s selections using canonical catalog UUIDs",
  (kind) => {
    const key = `${kind}:${uuid(101)}`;
    for (const id of [
      uuid(101),
      uuid(101).toUpperCase(),
      uuid(101).replaceAll("-", ""),
      `{${uuid(101)}}`,
      `urn:uuid:${uuid(101)}`,
    ]) {
      expect(readerColumnFeedKey({ kind, id })).toBe(key);
    }
  },
);

it("distinguishes built-in feeds and catalog kinds even when catalog UUIDs match", () => {
  const columns: ReaderColumn[] = [
    latest(1),
    { id: uuid(2), feed: { kind: "recommended" } },
    ...(["source", "topic", "tag"] as const).map((kind, index) => ({
      id: uuid(index + 3),
      feed: { kind, id: uuid(101) },
    })),
  ];
  expect(columns.map(({ feed }) => readerColumnFeedKey(feed))).toEqual([
    "latest",
    "recommended",
    `source:${uuid(101)}`,
    `topic:${uuid(101)}`,
    `tag:${uuid(101)}`,
  ]);
  expect(validateReaderPreferences({ ...settings, columns })).toBe(true);
  expect(normalizeReaderPreferences({ columns }).columns).toEqual(columns);
});

it.each(["latest", "recommended", "source", "topic", "tag"] as const)(
  "keeps the first %s selection and rejects duplicate-feed writes with different column IDs",
  (kind) => {
    const feed = kind === "latest" || kind === "recommended" ? { kind } : { kind, id: uuid(101) };
    const first: ReaderColumn = { id: uuid(1), feed };
    const duplicate = {
      id: uuid(2),
      feed: "id" in feed ? { ...feed, id: uuid(101).replaceAll("-", "").toUpperCase() } : feed,
    };
    const columns = [first, duplicate, topic(3)];
    const original = structuredClone(columns);
    expect(normalizeReaderPreferences({ columns }).columns).toEqual([first, topic(3)]);
    expect(columns).toEqual(original);
    expect(validateReaderPreferences({ ...settings, columns })).toBe(false);
    const target = storage();
    expect(writeGuestReaderPreferences({ view: "columns", columns }, target)).toBe(false);
    expect(target.setItem).not.toHaveBeenCalled();
  },
);

it("does not let a discarded duplicate consume a different selection's stable column ID", () => {
  const columns = [latest(1), latest(2), topic(2)];
  expect(normalizeReaderPreferences({ columns }).columns).toEqual([latest(1), topic(2)]);
});

it("rejects duplicate identities after UUID canonicalization", () => {
  const columns = [latest(1), { ...latest(1), id: uuid(1).replaceAll("-", "").toUpperCase() }];
  expect(validateReaderPreferences({ ...settings, columns })).toBe(false);
  expect(writeGuestReaderPreferences({ view: "columns", columns }, storage())).toBe(false);
});

it("keeps valid filter selections and restores defaults for malformed selections", () => {
  expect(
    normalizeReaderPreferences({
      ...settings,
      languages: ["fr", "en", "fr", 1, "unknown"],
      content_types: ["tutorial", "news", "tutorial", null, "removed"],
    }),
  ).toMatchObject({ languages: ["fr", "en"], content_types: ["tutorial", "news"] });
  expect(normalizeReaderPreferences({ languages: [], content_types: [null] })).toMatchObject({
    languages: ["en"],
  });
  expect(validateReaderPreferences(settings)).toBe(true);
});

it.each([
  null,
  [],
  { ...settings, view: "grid" },
  { ...settings, extra: true },
  { ...settings, languages: [] },
  { ...settings, languages: [null] },
  { ...settings, languages: ["unknown"] },
  { ...settings, content_types: [] },
  { ...settings, content_types: ["removed"] },
  { ...settings, columns: null },
  { ...settings, columns: {} },
])("rejects malformed mutation acknowledgement fields: %j", (value) => {
  expect(validateReaderPreferences(value)).toBe(false);
});

it.each(["cards", "compact", "stacked", "columns"])(
  "reads versioned %s without modifying storage or importing account fields",
  (view) => {
    const target = storage({
      [guestReaderPreferencesKey]: JSON.stringify({
        version: readerPreferencesVersion,
        view,
        columns: [latest(1)],
      }),
      [legacyGuestFeedViewKey]: "compact",
    });
    expect(readGuestReaderPreferences(target)).toEqual({
      value: { ...normalizeReaderPreferences(null), view, columns: [latest(1)] },
      unavailable: false,
    });
    expect(target.setItem).not.toHaveBeenCalled();
  },
);

it("reads legacy duplicate feeds once without rewriting the guest's stored record", () => {
  const raw = JSON.stringify({
    version: 1,
    view: "columns",
    columns: [latest(1), latest(2), topic(3)],
  });
  const target = storage({ [guestReaderPreferencesKey]: raw });
  expect(readGuestReaderPreferences(target).value.columns).toEqual([latest(1), topic(3)]);
  expect(target.values.get(guestReaderPreferencesKey)).toBe(raw);
  expect(target.setItem).not.toHaveBeenCalled();
  expect(guestColumnsInitialized(target)).toBe(true);
});

it.each(["cards", "compact", "stacked", "unknown", null])(
  "reads the legacy guest layout %s",
  (view) => {
    const target = storage(view === null ? {} : { [legacyGuestFeedViewKey]: view });
    expect(readGuestReaderPreferences(target).value).toMatchObject({
      view: view === "compact" || view === "stacked" ? view : "cards",
      columns: [],
    });
    expect(target.setItem).not.toHaveBeenCalled();
  },
);

it.each(["{invalid", "[]", "null", '{"view":"columns"}'])(
  "recovers malformed versioned storage using the legacy layout: %s",
  (value) => {
    const target = storage({
      [guestReaderPreferencesKey]: value,
      [legacyGuestFeedViewKey]: "compact",
    });
    expect(readGuestReaderPreferences(target)).toMatchObject({
      value: { view: "compact", columns: [] },
      unavailable: false,
    });
    expect(target.values.get(guestReaderPreferencesKey)).toBe(value);
  },
);

it.each([0, 2, "1", null])(
  "preserves unsupported storage version %j and blocks writes",
  (version) => {
    const raw = JSON.stringify({ version, view: "columns", columns: [latest(1)], future: true });
    const target = storage({
      [guestReaderPreferencesKey]: raw,
      [legacyGuestFeedViewKey]: "compact",
    });
    expect(readGuestReaderPreferences(target).unavailable).toBe(true);
    expect(writeGuestReaderPreferences({ view: "compact", columns: [] }, target)).toBe(false);
    expect(target.values.get(guestReaderPreferencesKey)).toBe(raw);
    expect(target.values.get(legacyGuestFeedViewKey)).toBe("compact");
    expect(target.setItem).not.toHaveBeenCalled();
  },
);

it("rechecks the storage version before writing after an earlier successful read", () => {
  const target = storage();
  const { value } = readGuestReaderPreferences(target);
  const future = JSON.stringify({ version: 2, extra: "retained" });
  target.values.set(guestReaderPreferencesKey, future);
  expect(writeGuestReaderPreferences(value, target)).toBe(false);
  expect(target.values.get(guestReaderPreferencesKey)).toBe(future);
});

it.each(["cards", "compact", "stacked", "columns"])(
  "writes a versioned %s workspace and compatible legacy layout",
  (view) => {
    const target = storage();
    const value = normalizeReaderPreferences({ ...settings, view, columns: [latest(1)] });
    expect(writeGuestReaderPreferences(value, target)).toBe(true);
    expect(JSON.parse(target.values.get(guestReaderPreferencesKey)!)).toEqual({
      version: 1,
      view,
      columns: [latest(1)],
      columns_initialized: true,
    });
    expect(target.values.get(legacyGuestFeedViewKey)).toBe(view);
    expect(readGuestReaderPreferences(target).value).toMatchObject({ view, columns: [latest(1)] });
  },
);

it("canonicalizes guest workspace identities before storing and supports explicit clearing", () => {
  const target = storage();
  expect(
    writeGuestReaderPreferences(
      { view: "cards", columns: [{ ...latest(1), id: uuid(1).toUpperCase() }] },
      target,
    ),
  ).toBe(true);
  expect(readGuestReaderPreferences(target).value.columns).toEqual([latest(1)]);
  expect(writeGuestReaderPreferences({ view: "cards", columns: [] }, target)).toBe(true);
  expect(readGuestReaderPreferences(target).value.columns).toEqual([]);
});

it.each([
  [{ version: 1, view: "cards" }, false],
  [{ version: 1, view: "columns", columns: [] }, true],
  [{ version: 1, columns: [], columns_initialized: false }, false],
  [{ version: 1, columns: [], columns_initialized: true }, true],
  [{ version: 1, columns: [latest(1)], columns_initialized: false }, true],
  [{ version: 1, columns: [], columns_initialized: "false" }, true],
  [{ version: 2, columns: [latest(1)], columns_initialized: true }, false],
])(
  "recognizes guest first use without mistaking a legacy empty workspace for an unused one: %j",
  (stored, initialized) => {
    const target = storage({ [guestReaderPreferencesKey]: JSON.stringify(stored) });
    expect(guestColumnsInitialized(target)).toBe(initialized);
    expect(target.setItem).not.toHaveBeenCalled();
  },
);

it("leaves first-use eligibility intact when only a guest layout is saved", () => {
  const target = storage();
  expect(guestColumnsInitialized(target)).toBe(false);
  expect(writeGuestReaderPreferences({ view: "stacked", columns: [] }, target, false)).toBe(true);
  expect(JSON.parse(target.values.get(guestReaderPreferencesKey)!)).toEqual({
    version: 1,
    view: "stacked",
    columns: [],
    columns_initialized: false,
  });
  expect(guestColumnsInitialized(target)).toBe(false);
  expect(writeGuestReaderPreferences({ view: "columns", columns: [] }, target)).toBe(true);
  expect(guestColumnsInitialized(target)).toBe(true);
  expect(readGuestReaderPreferences(target).value.columns).toEqual([]);
});

it.each([
  { version: 1, columns: [] },
  { version: 1, columns: [], columns_initialized: true },
])(
  "does not unset a previously initialized guest workspace while changing layouts: %j",
  (stored) => {
    const target = storage({ [guestReaderPreferencesKey]: JSON.stringify(stored) });
    expect(writeGuestReaderPreferences({ view: "compact", columns: [] }, target, false)).toBe(true);
    expect(guestColumnsInitialized(target)).toBe(true);
    expect(JSON.parse(target.values.get(guestReaderPreferencesKey)!).columns_initialized).toBe(
      true,
    );
  },
);

it("marks nonempty guest columns initialized even if a layout caller passes false", () => {
  const target = storage();
  expect(
    writeGuestReaderPreferences({ view: "columns", columns: [latest(1)] }, target, false),
  ).toBe(true);
  expect(guestColumnsInitialized(target)).toBe(true);
});

it("keeps denied-storage and server-rendered reads usable without claiming a successful save", () => {
  const target = storage();
  target.getItem.mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  expect(readGuestReaderPreferences(target)).toMatchObject({
    value: { view: "cards", columns: [] },
    unavailable: false,
  });
  expect(writeGuestReaderPreferences({ view: "compact" }, target)).toBe(false);
  expect(target.setItem).not.toHaveBeenCalled();
  vi.stubGlobal("window", undefined);
  expect(readGuestReaderPreferences().unavailable).toBe(false);
  expect(writeGuestReaderPreferences({ view: "compact" })).toBe(false);
});

it("reports a failed write when the versioned or legacy storage write is blocked", () => {
  for (const blockedKey of [guestReaderPreferencesKey, legacyGuestFeedViewKey]) {
    const target = storage();
    target.setItem.mockImplementation((key, value) => {
      if (key === blockedKey) throw new Error("Quota exceeded");
      return target.values.set(key, value);
    });
    expect(writeGuestReaderPreferences({ view: "compact" }, target)).toBe(false);
  }
});

it("accepts a Columns receipt with ordered columns before emitting success effects", async () => {
  const value = normalizeReaderPreferences({ ...settings, view: "columns", columns: [latest(1)] });
  const fetcher = vi.fn().mockResolvedValue(Response.json(value));
  const dispatch = vi.spyOn(window, "dispatchEvent");
  vi.stubGlobal("fetch", fetcher);
  expect(await saveFeedSettings(value, { csrfToken: "csrf" })).toEqual(value);
  expect(fetcher.mock.calls[0][1]).toMatchObject({
    method: "PUT",
    body: JSON.stringify(value),
    headers: { "X-CSRF-Token": "csrf" },
  });
  expect(dispatch.mock.calls.map(([event]) => event.type)).toEqual([
    "devfeed:interests-changed",
    "devfeed:analytics",
  ]);
});

it.each([
  [latest(1), latest(1)],
  [latest(1), latest(2)],
])("rejects duplicate column receipts without emitting success effects: %j", async (...columns) => {
  const value = normalizeReaderPreferences(settings);
  const dispatch = vi.spyOn(window, "dispatchEvent");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...value, columns })));
  await expect(saveFeedSettings(value, { csrfToken: "csrf" })).rejects.toThrow(
    "Invalid mutation acknowledgement",
  );
  expect(dispatch).not.toHaveBeenCalled();
});
