"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { userRequest } from "@/lib/user";
import {
  normalizeReaderColumns,
  validateReaderColumns,
  type ReaderColumn,
} from "@/lib/reader-preferences";
import {
  columnSettingsPath,
  saveReaderColumns,
  validReaderColumnsState,
  type ColumnWorkspaceScope,
  type ReaderColumnsState,
  type StarterColumnsMode,
} from "@/lib/user-mutations";
import { useFeedPreferences } from "./feed-preferences";
import { useUser } from "./user-account";

type ColumnsPreferences = Pick<
  ReturnType<typeof useFeedPreferences>,
  | "columns"
  | "columnsInitialized"
  | "loading"
  | "busy"
  | "unavailable"
  | "error"
  | "setColumns"
  | "initializeColumns"
  | "refresh"
>;
const Context = createContext<ColumnsPreferences | null>(null);
const Scope = createContext<ColumnWorkspaceScope>("latest");

/** Global layout/filter preferences remain shared; each main page owns its columns. */
export function useColumnPreferences() {
  const shared = useFeedPreferences();
  const scoped = useContext(Context);
  const workspace = useContext(Scope);
  return { ...shared, ...scoped, workspace };
}

export function ColumnPreferencesProvider({
  workspace,
  children,
}: {
  workspace: ColumnWorkspaceScope;
  children: React.ReactNode;
}) {
  const { user } = useUser();
  return workspace === "my_feed" && user ? (
    <Scope.Provider value="my_feed">
      <PersonalColumns>{children}</PersonalColumns>
    </Scope.Provider>
  ) : (
    children
  );
}

function PersonalColumns({ children }: { children: React.ReactNode }) {
  const { user, loading: checking, sessionRevision } = useUser();
  const userId = user?.user_id;
  const csrfToken = user?.csrf_token ?? "";
  const [revision, setRevision] = useState(0);
  const key = `${userId}:${sessionRevision}:${revision}`;
  const [state, setState] = useState<{ key: string; value: ReaderColumnsState | null } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<{ controller: AbortController; ready: boolean } | null>(null);
  const saving = useRef(false);
  useEffect(() => {
    if (checking || !userId) return;
    const controller = new AbortController();
    const pending = { controller, ready: false };
    request.current = pending;
    saving.current = false;
    void Promise.resolve()
      .then(() => {
        controller.signal.throwIfAborted();
        setBusy(true);
        setError("");
        return userRequest<ReaderColumnsState>(columnSettingsPath("my_feed"), {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
      })
      .then((value) => {
        if (!validReaderColumnsState(value)) throw new Error("Invalid workspace state");
        if (!controller.signal.aborted) {
          pending.ready = true;
          setState({ key, value });
          setBusy(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          pending.ready = true;
          setState({ key, value: null });
          setBusy(false);
        }
      });
    return () => controller.abort();
    // Profile labels can change independently of the owner and confirmed session.
  }, [checking, key, userId]);
  const current = state?.key === key ? state : null;
  const persist = useCallback(
    async (
      columns: ReaderColumn[],
      mode: StarterColumnsMode | "replace",
      callerSignal?: AbortSignal,
    ) => {
      const pending = request.current;
      const controller = pending?.controller;
      if (
        !userId ||
        checking ||
        !current?.value ||
        saving.current ||
        !pending?.ready ||
        !controller ||
        controller.signal.aborted ||
        callerSignal?.aborted
      )
        return false;
      if (!validateReaderColumns(columns)) return false;
      const signal = AbortSignal.any([
        controller.signal,
        AbortSignal.timeout(15000),
        ...(callerSignal ? [callerSignal] : []),
      ]);
      saving.current = true;
      setBusy(true);
      setError("");
      try {
        const result = await saveReaderColumns(
          normalizeReaderColumns(columns),
          mode,
          { csrfToken, signal },
          "my_feed",
        );
        if (signal.aborted) return false;
        setState({ key, value: result });
        return true;
      } catch {
        if (!controller.signal.aborted && !callerSignal?.aborted)
          setError("Couldn’t save your columns. Please try again.");
        return false;
      } finally {
        if (request.current === pending) {
          saving.current = false;
          if (!controller.signal.aborted) setBusy(false);
        }
      }
    },
    [checking, current, key, userId, csrfToken],
  );
  const initializeColumns = useCallback(
    (columns: ReaderColumn[], mode: StarterColumnsMode, signal?: AbortSignal) =>
      persist(columns, mode, signal),
    [persist],
  );
  return (
    <Context.Provider
      value={{
        columns: current?.value?.columns ?? [],
        columnsInitialized: current?.value?.initialized ?? null,
        loading: !current,
        unavailable: !!current && !current.value,
        busy,
        error,
        setColumns: (columns) => persist(columns, "replace"),
        initializeColumns,
        refresh: () => setRevision((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
