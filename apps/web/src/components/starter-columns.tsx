"use client";

import { useEffect, useState } from "react";
import { Columns3 } from "lucide-react";
import { readerRequest } from "@/lib/reader-runtime";
import { createReaderColumnId } from "@/lib/reader-column-id";
import {
  guestColumnsInitialized,
  normalizeReaderPreferences,
  validateReaderPreferences,
  type ReaderColumn,
} from "@/lib/reader-preferences";
import type { ColumnRequestQueue } from "@/lib/column-feed";
import { userRequest } from "@/lib/user";
import {
  validReaderColumnsState,
  columnSettingsPath,
  type ReaderColumnsState,
  type StarterColumnsMode,
} from "@/lib/user-mutations";
import { useColumnPreferences } from "./column-preferences";
import { useUser } from "./user-account";
import styles from "./columns-workspace.module.css";

/** Populate only an untouched workspace; an intentional empty workspace offers an explicit reset. */
export function StarterColumns({ queue }: { queue: ColumnRequestQueue }) {
  const { columnsInitialized, content_types, languages, initializeColumns, workspace } =
    useColumnPreferences();
  const { user, loading: checking } = useUser();
  const [command, setCommand] = useState<{ mode: StarterColumnsMode; attempt: number }>({
    mode: "initialize",
    attempt: 0,
  });
  const [result, setResult] = useState<{ key: string; error: boolean } | null>(null);
  const preferences = `${content_types.join(",")}:${languages.join(",")}`;
  const key = `${workspace}:${command.mode}:${command.attempt}:${preferences}`;
  useEffect(() => {
    if (checking) return;
    const controller = new AbortController();
    const { signal } = controller;
    const load = async () => {
      if (command.mode === "initialize") {
        if (columnsInitialized === true && workspace === "my_feed") {
          if (!signal.aborted) setResult({ key, error: false });
          return;
        }
        if (user) {
          const state = await queue.run(async (requestSignal) => {
            const value = await userRequest<ReaderColumnsState>(columnSettingsPath(workspace), {
              signal: requestSignal,
            });
            if (!validReaderColumnsState(value)) throw new Error("Invalid workspace state");
            return value;
          }, signal);
          if (signal.aborted) return;
          if (state.initialized) {
            // A workspace edited on another device wins over this tab's older empty snapshot.
            if (state.columns.length && !(await initializeColumns([], "initialize", signal)))
              throw new Error("Could not restore workspace");
            if (!signal.aborted) setResult({ key, error: false });
            return;
          }
        } else if (columnsInitialized || guestColumnsInitialized()) {
          await initializeColumns([], "initialize", signal);
          if (!signal.aborted) setResult({ key, error: false });
          return;
        }
      }
      if (workspace === "my_feed" && user) {
        const feeds = await queue.run(async (requestSignal) => {
          const value = await userRequest<{ feeds: ReaderColumn["feed"][] }>(
            "settings/columns/starters",
            {
              signal: requestSignal,
            },
          );
          if (
            !Array.isArray(value.feeds) ||
            value.feeds.length > 4 ||
            value.feeds.some((feed) => !feed || !["topic", "source", "tag"].includes(feed.kind))
          )
            throw new Error("Invalid personal starter feeds");
          return [
            { kind: "recommended" as const },
            ...(value.feeds.length ? value.feeds : [{ kind: "latest" as const }]),
          ];
        }, signal);
        if (signal.aborted) return;
        const candidate = { columns: feeds.map((feed) => ({ id: createReaderColumnId(), feed })) };
        if (!validateReaderPreferences({ ...candidate, view: "columns", content_types, languages }))
          throw new Error("Invalid personal starter columns");
        const columns = normalizeReaderPreferences(candidate).columns;
        if (!(await initializeColumns(columns, command.mode, signal)))
          throw new Error("Could not save personal starter columns");
        if (!signal.aborted) setResult({ key, error: false });
        return;
      }
      const topics = await queue.run(async (requestSignal) => {
        const response = await readerRequest("/api/v1/topics?offset=0&limit=2&sort=articles", {
          signal: requestSignal,
        });
        if (!response.ok) throw new Error("Could not load starter topics");
        const value = (await response.json()) as { items: { id: string }[] };
        if (!Array.isArray(value.items)) throw new Error("Invalid topic catalog");
        return value.items
          .slice(0, 2)
          .filter(
            (topic, index, items) =>
              items.findIndex((candidate) => candidate.id === topic.id) === index,
          );
      }, signal);
      if (signal.aborted) return;
      const feeds: ReaderColumn["feed"][] = [
        { kind: "latest" },
        ...topics.map(({ id }) => ({ kind: "topic" as const, id })),
        ...(user ? [{ kind: "recommended" as const }] : []),
      ];
      // Catalog IDs pass the same canonical validation as reader-owned selections.
      const columns = normalizeReaderPreferences({
        columns: feeds.map((feed) => ({ id: createReaderColumnId(), feed })),
      }).columns;
      if (!(await initializeColumns(columns, command.mode, signal)))
        throw new Error("Could not save starter columns");
      if (!signal.aborted) setResult({ key, error: false });
    };
    void Promise.resolve()
      .then(load)
      .catch(() => {
        if (!signal.aborted) setResult({ key, error: true });
      });
    return () => controller.abort();
  }, [
    checking,
    command.mode,
    columnsInitialized,
    initializeColumns,
    key,
    queue,
    user,
    workspace,
    content_types,
    languages,
  ]);
  const pending = checking || result?.key !== key;
  return (
    <div className={styles.empty}>
      <Columns3 size={38} aria-hidden="true" />
      <h2>Build your reading workspace</h2>
      {pending ? (
        <p role="status">Preparing starter columns…</p>
      ) : result?.error ? (
        <>
          <p role="status">Couldn’t prepare starter columns. Try again or add your own.</p>
          <button
            className="button"
            onClick={() =>
              setCommand((previous) => ({ ...previous, attempt: previous.attempt + 1 }))
            }
          >
            Try again
          </button>
        </>
      ) : (
        <>
          <p>
            {workspace === "my_feed"
              ? "Add your own feeds, or start with your follows and likes."
              : "Add your own feeds, or start with Latest and popular topics."}
          </p>
          <button
            className="button"
            onClick={() =>
              setCommand((previous) => ({ mode: "restore_empty", attempt: previous.attempt + 1 }))
            }
          >
            Use starter columns
          </button>
        </>
      )}
    </div>
  );
}
