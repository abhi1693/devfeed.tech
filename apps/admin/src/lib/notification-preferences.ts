import type { Preference } from "@chimely/client";
export const notificationCategories = [
  { id: "jobs.ingestion", label: "Feed ingestion" },
  { id: "jobs.article-enrichment", label: "Article enrichment" },
  { id: "jobs.images", label: "Image lookup" },
  { id: "jobs.source-enrichment", label: "Source enrichment" },
  { id: "jobs.analysis", label: "Article analysis" },
  { id: "jobs.topic-analysis", label: "Topic enrichment" },
  { id: "jobs.relationship-research", label: "Relationship research" },
];
export const notificationEvents = [{ id: "error", label: "Failures" }, { id: "warning", label: "Retries" }, { id: "success", label: "Completions" }];
export const notificationCategoryLabels = Object.fromEntries(notificationCategories.flatMap(category => [[category.id, category.label], ...notificationEvents.map(event => [`${category.id}.${event.id}`, `${category.label} · ${event.label}`])]));
export const preferencesChanged = "devfeed:notification-preferences";
export function notificationChoices(preferences: Preference[]): Record<string, boolean> {
  const saved = new Map(preferences.filter(value => value.channel === "in_app").map(value => [value.category, value.enabled]));
  return Object.fromEntries(notificationCategories.flatMap(category => notificationEvents.map(event => {
    const key = `${category.id}.${event.id}`;
    return [key, saved.get(key) ?? saved.get(category.id) ?? true];
  })));
}
export function notificationPreferences(choices: Record<string, boolean>): Preference[] {
  return notificationCategories.flatMap(category => [
    { category: category.id, channel: "in_app" as const, enabled: notificationEvents.some(event => choices[`${category.id}.${event.id}`] !== false) },
    ...notificationEvents.map(event => ({ category: `${category.id}.${event.id}`, channel: "in_app" as const, enabled: choices[`${category.id}.${event.id}`] !== false })),
  ]);
}

/** Expand an existing category choice when new event-level categories first appear. */
export async function inheritNotificationPreferences(client: Pick<import("@chimely/client").ChimelyClient, "getPreferences" | "setPreferences">) {
  const preferences = await client.getPreferences();
  const saved = new Map(preferences.filter(item => item.channel === "in_app").map(item => [item.category, item.enabled]));
  const missing: Preference[] = [];
  for (const category of notificationCategories) {
    const parent = saved.get(category.id) ?? (category.id === "jobs.relationship-research" ? saved.get("jobs.topic-analysis") : undefined);
    if (parent === undefined) continue;
    for (const event of notificationEvents) {
      const key = `${category.id}.${event.id}`;
      if (!saved.has(key)) missing.push({ category: key, channel: "in_app", enabled: parent });
    }
  }
  return missing.length ? client.setPreferences(missing) : preferences;
}
