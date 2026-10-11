const key = "devfeed:partner-placement-preferences:v1";
const day = 86_400_000;
const retention = 90 * day;
type Entry = { id: string; day: string; count: number; seen: number };
type Preferences = { version: 1; entries: Entry[] };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const date = () => new Date().toISOString().slice(0, 10);

function read(): Preferences | null {
  try {
    const stored = localStorage.getItem(key);
    if (stored === null) return { version: 1, entries: [] };
    const value = JSON.parse(stored) as Preferences;
    if (
      value.version !== 1 ||
      !Array.isArray(value.entries) ||
      value.entries.length > 100 ||
      !value.entries.every(
        (entry) =>
          typeof entry.id === "string" &&
          uuid.test(entry.id) &&
          typeof entry.day === "string" &&
          /^\d{4}-\d{2}-\d{2}$/.test(entry.day) &&
          Number.isSafeInteger(entry.count) &&
          entry.count >= 0 &&
          Number.isFinite(entry.seen) &&
          entry.seen >= 0,
      )
    )
      return null;
    value.entries = value.entries
      .filter((entry) => entry.seen > Date.now() - retention)
      .map(({ id, day, count, seen }) => ({ id, day, count, seen }));
    return value;
  } catch {
    // Optional view history never controls product eligibility or disclosure.
    return null;
  }
}

export function recordPlacementView(id: string) {
  const value = read();
  if (!value) return false;
  const existing = value.entries.find((entry) => entry.id === id);
  const entry: Entry = existing ?? { id, day: date(), count: 0, seen: 0 };
  if (entry.day !== date()) {
    entry.day = date();
    entry.count = 0;
  }
  entry.count = Math.min(entry.count + 1, Number.MAX_SAFE_INTEGER);
  entry.seen = Date.now();
  value.entries = [entry, ...value.entries.filter((item) => item.id !== id)].slice(0, 100);
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
