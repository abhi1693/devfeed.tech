// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  guestReaderPreferencesKey,
  legacyGuestFeedViewKey,
  maxReaderColumns,
  normalizeReaderPreferences,
  readGuestReaderPreferences,
  readerPreferencesVersion,
  validateReaderPreferences,
  writeGuestReaderPreferences,
  type ReaderColumn,
} from "@/lib/reader-preferences";
import { normalizeFeedView } from "@/lib/reader-layouts";
import { saveFeedSettings } from "@/lib/user-mutations";

const uuid = (index: number) => `abcdef00-0000-0000-0000-${index.toString(16).padStart(12, "0")}`;
const latest = (index: number): ReaderColumn => ({ id: uuid(index), feed: { kind: "latest" } });
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
    expect(normalizeFeedView(result.view)).toBe(view === "columns" ? "cards" : view);
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
    // Several independent columns can deliberately select the same feed.
    { id: uuid(6), feed: { kind: "source", id: uuid(102) } },
  ];
  const result = normalizeReaderPreferences({ ...settings, columns });
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
    expect(normalizeReaderPreferences(value).columns).toEqual([latest(9)]);
    expect(validateReaderPreferences(value)).toBe(false);
  },
);

it("retains the first valid duplicate and bounds normalized workspaces to 12 columns", () => {
  const columns = [
    { id: uuid(1), feed: { kind: "removed" } },
    latest(1),
    { id: uuid(1).toUpperCase(), feed: { kind: "recommended" } },
    ...Array.from({ length: maxReaderColumns + 3 }, (_, index) => latest(index + 2)),
  ];
  expect(normalizeReaderPreferences({ columns }).columns).toEqual(
    Array.from({ length: maxReaderColumns }, (_, index) => latest(index + 1)),
  );
  expect(validateReaderPreferences({ ...settings, columns })).toBe(false);
  expect(
    validateReaderPreferences({ ...settings, columns: columns.slice(3, 3 + maxReaderColumns + 1) }),
  ).toBe(false);
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
    });
    expect(target.values.get(legacyGuestFeedViewKey)).toBe(view === "columns" ? "cards" : view);
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

it("accepts a future-layout receipt with ordered columns before emitting success effects", async () => {
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

it("rejects an invalid column receipt without emitting success effects", async () => {
  const value = normalizeReaderPreferences(settings);
  const dispatch = vi.spyOn(window, "dispatchEvent");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ ...value, columns: [latest(1), latest(1)] })),
  );
  await expect(saveFeedSettings(value, { csrfToken: "csrf" })).rejects.toThrow(
    "Invalid mutation acknowledgement",
  );
  expect(dispatch).not.toHaveBeenCalled();
});
