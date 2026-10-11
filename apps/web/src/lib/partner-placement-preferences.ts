const key = "devfeed:partner-placement-preferences:v1";
const day = 86_400_000;
const retention = 90 * day;
const cooldown = 30 * 60_000;
type Entry = { id: string; day: string; count: number; cap: number; seen: number; hidden: number };
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
          Number.isSafeInteger(entry.cap) &&
          entry.cap >= 1 &&
          Number.isFinite(entry.seen) &&
          entry.seen >= 0 &&
          Number.isFinite(entry.hidden) &&
          entry.hidden >= 0,
      )
    )
      return null;
    value.entries = value.entries.filter(
      (entry) => Math.max(entry.seen, entry.hidden) > Date.now() - retention,
    );
    return value;
  } catch {
    // Respect suppression and caps even when persistence is unavailable or corrupt.
    return null;
  }
}

export function placementExclusions(currentCaps?: ReadonlyMap<string, number>): string[] | null {
  const value = read();
  if (!value) return null;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    return null;
  }
  return value.entries
    .filter(
      (entry) =>
        entry.hidden > Date.now() ||
        entry.seen > Date.now() - cooldown ||
        (entry.day === date() && entry.count >= (currentCaps?.get(entry.id) ?? entry.cap)),
    )
    .map((entry) => entry.id);
}

function update(id: string, cap: number, hide: boolean) {
  const value = read();
  if (!value) return false;
  const existing = value.entries.find((entry) => entry.id === id);
  const entry: Entry = existing ?? { id, day: date(), count: 0, cap, seen: 0, hidden: 0 };
  if (entry.day !== date()) {
    entry.day = date();
    entry.count = 0;
  }
  entry.cap = cap;
  if (hide) entry.hidden = Date.now() + retention;
  else {
    entry.count += 1;
    entry.seen = Date.now();
  }
  value.entries = [entry, ...value.entries.filter((item) => item.id !== id)].slice(0, 100);
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export const recordPlacementView = (id: string, cap: number) => update(id, cap, false);
export const hidePartnerProduct = (id: string, cap: number) => update(id, cap, true);
