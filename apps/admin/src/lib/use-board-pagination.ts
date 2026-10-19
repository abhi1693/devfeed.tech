"use client";

import { useEffect, useRef, useState } from "react";
import { notifyFailure } from "./notifications";

/** Keep additional pages tied to the exact polled snapshot that requested them. */
export function useBoardPagination<
  Snapshot,
  Stage extends string,
  Column extends { stage: Stage; next_cursor: unknown },
>({
  snapshot,
  load,
  merge,
  failureMessage,
}: {
  snapshot: Snapshot;
  load: (column: Column, signal: AbortSignal) => Promise<Column>;
  merge: (previous: Column, next: Column) => Column;
  failureMessage: string;
}) {
  const [additional, setAdditional] = useState<{
    snapshot: Snapshot;
    columns: Partial<Record<Stage, Column>>;
  }>();
  const [loading, setLoading] = useState<{ snapshot: Snapshot; stage: Stage }>();
  const [failure, setFailure] = useState<{ snapshot: Snapshot; stage: Stage }>();
  const current = useRef(snapshot);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    current.current = snapshot;
    return () => request.current?.abort();
  }, [snapshot]);
  async function more(column: Column) {
    if (column.next_cursor == null || (request.current && !request.current.signal.aborted)) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading({ snapshot, stage: column.stage });
    setFailure(undefined);
    try {
      const next = await load(column, controller.signal);
      if (!controller.signal.aborted && current.current === snapshot) {
        setAdditional((previous) => ({
          snapshot,
          columns: {
            ...(previous?.snapshot === snapshot ? previous.columns : {}),
            [column.stage]: merge(column, next),
          },
        }));
      }
    } catch (cause) {
      if (!controller.signal.aborted && current.current === snapshot) {
        notifyFailure(cause, failureMessage);
        setFailure({ snapshot, stage: column.stage });
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setLoading(undefined);
      }
    }
  }
  const columns: Partial<Record<Stage, Column>> =
    additional?.snapshot === snapshot ? additional.columns : {};
  return {
    columns,
    loading: loading?.snapshot === snapshot ? loading.stage : undefined,
    error: failure?.snapshot === snapshot ? failure.stage : undefined,
    more,
  };
}
