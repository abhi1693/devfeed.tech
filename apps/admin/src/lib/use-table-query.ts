"use client";

import { useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { useSettings } from "./use-settings";
import { notifyFailure } from "./notifications";

const allowed = new Set(["interests", "view", "q", "sort", "limit", "status", "review_status", "publication_status", "approval_status", "enabled", "kind", "source", "action", "analysis", "missing", "topic_id", "job_id", "batch_id"]);

/** Explicit URLs win over personal defaults; offsets and row selection are never saved. */
export function useTableQuery(key: string) {
  const raw = useSearchParams().toString();
  const { settings, persistent, saveTable } = useSettings();
  const { remember_filters: filters, remember_sort: sort, page_size: pageSize } = settings.defaults;
  const remembered = settings.tables[key]?.query ?? {};
  const permitted = (name: string) => allowed.has(name) && (name === "limit" || (name === "sort" ? sort : filters));
  const query = new URLSearchParams(raw || Object.entries(remembered).filter((entry): entry is [string, string] => permitted(entry[0]) && typeof entry[1] === "string"));
  if (!query.has("limit")) query.set("limit", String(pageSize));
  const next = JSON.stringify(Object.fromEntries([...new URLSearchParams(raw)].filter(([name]) => permitted(name))));
  const previous = JSON.stringify(remembered);
  useEffect(() => {
    if (!persistent || !raw || next === previous) return;
    const timer = window.setTimeout(() => { void saveTable(key, { query: JSON.parse(next) }).catch(error => notifyFailure(error, "Could not save table preferences")); }, 500);
    return () => window.clearTimeout(timer);
  }, [persistent, raw, next, previous, key, saveTable]);
  return query;
}
