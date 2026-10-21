"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readerRequest } from "./reader-runtime";
import { AccountError, userRequest } from "./user";
import type { ReaderColumnFeed } from "./reader-preferences";
import { columnFeedLimits, type ColumnPage, type ColumnRequestQueue } from "./column-feed";
import { uniquePageItems } from "./use-infinite-pages";

type State = {
  pages: ColumnPage[];
  cursor: string | null;
  loading: boolean;
  error: AccountError | null;
};
const initial: State = { pages: [], cursor: null, loading: true, error: null };

/** Each mounted selection owns its pages; neither its neighbors nor preview URLs enter identity. */
export function useColumnFeed(
  feed: ReaderColumnFeed,
  slug: string | undefined,
  queue: ColumnRequestQueue,
) {
  const kind = feed.kind;
  const id = "id" in feed ? feed.id : "";
  const [state, setState] = useState<State>(initial);
  const current = useRef(state);
  const request = useRef<AbortController | null>(null);
  const loaded = useRef(new Set<string>());
  const scope = useMemo(() => ({ kind, id, slug, queue }), [kind, id, slug, queue]);
  const lifetime = useRef<typeof scope | null>(null);
  const update = useCallback((value: State) => {
    current.current = value;
    setState(value);
  }, []);
  const load = useCallback(
    async (replace = false) => {
      const previous = current.current;
      if (
        lifetime.current !== scope ||
        request.current ||
        (!replace &&
          (previous.cursor === null || previous.pages.length >= columnFeedLimits.retainedPages))
      )
        return;
      const controller = new AbortController();
      request.current = controller;
      if (replace) loaded.current.clear();
      const cursor = replace ? "" : previous.cursor!;
      update({ ...(replace ? initial : previous), loading: true, error: null });
      try {
        const page = await queue.run(async (signal) => {
          const params = new URLSearchParams({ limit: String(columnFeedLimits.pageSize) });
          if (cursor) params.set("cursor", cursor);
          if (kind === "latest") params.set("diverse", "true");
          if (kind === "source") params.set("source_id", id);
          if (kind === "topic") params.set("topic", id);
          if (kind === "tag") {
            if (!slug) throw new AccountError(404);
            params.set("tag", slug);
          }
          let value: ColumnPage;
          if (kind === "recommended")
            value = await userRequest<ColumnPage>(`feed?${params}`, { signal });
          else {
            const response = await readerRequest(`/api/v1/feed?${params}`, {
              signal,
              cache: "no-store",
            });
            if (!response.ok) throw new AccountError(response.status);
            value = (await response.json()) as ColumnPage;
          }
          if (
            !Array.isArray(value.items) ||
            (value.next_cursor !== null && typeof value.next_cursor !== "string")
          )
            throw new AccountError(502);
          return { ...value, items: value.items.slice(0, columnFeedLimits.pageSize) };
        }, controller.signal);
        if (controller.signal.aborted) return;
        const seen = new Set(
          (replace ? [] : previous.pages).flatMap((batch) => batch.items.map((item) => item.id)),
        );
        const items = uniquePageItems(page.items, (item) => item.id).filter(
          (item) => !seen.has(item.id),
        );
        loaded.current.add(cursor);
        const next = page.next_cursor;
        update({
          pages: [...(replace ? [] : previous.pages), { ...page, items }],
          cursor: next && !loaded.current.has(next) ? next : null,
          loading: false,
          error: null,
        });
        return items[0]?.slug;
      } catch (cause) {
        if (!controller.signal.aborted)
          update({
            ...(replace ? initial : previous),
            loading: false,
            error: cause instanceof AccountError ? cause : new AccountError(503),
          });
      } finally {
        if (request.current === controller) request.current = null;
      }
    },
    [kind, id, slug, queue, update, scope],
  );
  useEffect(() => {
    lifetime.current = scope;
    void load(true);
    return () => {
      lifetime.current = null;
      request.current?.abort();
      request.current = null;
    };
  }, [load, scope]);
  const loadMore = useCallback(() => load(current.current.pages.length === 0), [load]);
  const refresh = useCallback(() => {
    request.current?.abort();
    request.current = null;
    void load(true);
  }, [load]);
  const articles = useMemo(() => state.pages.flatMap((page) => page.items), [state.pages]);
  return {
    ...state,
    articles,
    limited: state.pages.length >= columnFeedLimits.retainedPages && state.cursor !== null,
    loadMore,
    refresh,
  };
}
