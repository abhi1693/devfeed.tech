"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { contentTypes } from "@/lib/feed-query";
import { userRequest, type FeedDisplay } from "@/lib/user";
import { saveFeedSettings } from "@/lib/user-mutations";
import { normalizeFeedView, type FeedViewPreference } from "@/lib/reader-layouts";
import {
  normalizeReaderPreferences,
  readGuestReaderPreferences,
  validateReaderPreferences,
  writeGuestReaderPreferences,
  type ReaderColumn,
  type ReaderPreferences,
} from "@/lib/reader-preferences";
import { useUser } from "./user-account";

type State = {
  owner: string;
  requestKey: string;
  value: ReaderPreferences | null;
  unavailable: boolean;
};
const Context = createContext<{
  view: FeedViewPreference;
  storedView: FeedDisplay["view"];
  columns: ReaderColumn[];
  content_types: FeedDisplay["content_types"];
  languages: string[];
  loading: boolean;
  busy: boolean;
  unavailable: boolean;
  error: string;
  save: (settings: Partial<FeedDisplay>) => Promise<boolean>;
  setView: (view: FeedViewPreference) => Promise<boolean>;
  setColumns: (columns: ReaderColumn[]) => Promise<boolean>;
  refresh: () => void;
}>({
  view: "cards",
  storedView: "cards",
  columns: [],
  content_types: [...contentTypes],
  languages: ["en"],
  loading: true,
  busy: false,
  unavailable: false,
  error: "",
  save: async () => false,
  setView: async () => false,
  setColumns: async () => false,
  refresh: () => {},
});
export const useFeedPreferences = () => useContext(Context);

export function FeedPreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user, loading, sessionRevision } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<State | null>(null);
  const [revision, setRevision] = useState(0);
  const requestKey = `${owner}:${revision}:${sessionRevision}`;
  const [saving, setSaving] = useState<string | null>(null);
  const savingRef = useRef(false);
  const account = useRef<{ controller: AbortController; ready: boolean } | null>(null);
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null);
  useEffect(() => {
    if (loading) return;
    const controller = new AbortController();
    const request = { controller, ready: false };
    account.current = request;
    async function load() {
      // Account preferences always win on sign-in, including a new account's defaults.
      // Guest workspace stays in its own storage and returns on sign-out.
      const loaded =
        owner === "guest"
          ? readGuestReaderPreferences()
          : {
              value: normalizeReaderPreferences(
                await userRequest<FeedDisplay>("settings/feed", {
                  signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
                }),
              ),
              unavailable: false,
            };
      if (!controller.signal.aborted) {
        request.ready = true;
        setState({ owner, requestKey, ...loaded });
      }
    }
    void load().catch(() => {
      if (!controller.signal.aborted) {
        request.ready = true;
        setState({ owner, requestKey, value: null, unavailable: true });
      }
    });
    return () => controller.abort();
  }, [owner, loading, requestKey]);
  const current = state?.owner === owner ? state : null;
  async function persist(settings: Partial<FeedDisplay>, optimistic = false) {
    if (
      savingRef.current ||
      loading ||
      !current?.value ||
      current.unavailable ||
      current.requestKey !== requestKey
    )
      return false;
    const request = account.current;
    if (!request?.ready || request.controller.signal.aborted) return false;
    const { controller } = request;
    // Merge against stored preferences, rather than the effective Grid/List fallback,
    // so changing filters cannot discard a future layout or its ordered workspace.
    const candidate = { ...current.value, ...settings };
    if (!validateReaderPreferences(candidate)) return false;
    const value = normalizeReaderPreferences(candidate);
    const previous = current;
    savingRef.current = true;
    setSaving(owner);
    setFailure(null);
    if (optimistic) setState({ owner, requestKey, value, unavailable: false });
    try {
      if (!user) {
        const saved = writeGuestReaderPreferences(value);
        // Storage restrictions still allow a temporary layout within this tab.
        setState({ owner, requestKey, value, unavailable: false });
        if (!saved)
          setFailure({ owner, message: "Couldn’t save your reader preferences in this browser." });
        return saved;
      }
      const result = await saveFeedSettings(value, {
        csrfToken: user.csrf_token,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return false;
      setState((latest) =>
        latest?.owner === owner
          ? { owner, requestKey, value: normalizeReaderPreferences(result), unavailable: false }
          : latest,
      );
      return true;
    } catch {
      if (!controller.signal.aborted) {
        if (optimistic)
          setState((latest) =>
            latest?.owner === owner && latest.value === value ? previous : latest,
          );
        setFailure({ owner, message: "Couldn’t save your feed settings. Please try again." });
      }
      return false;
    } finally {
      savingRef.current = false;
      setSaving((latest) => (latest === owner ? null : latest));
    }
  }
  async function setView(view: FeedViewPreference) {
    if (view === current?.value?.view) return false;
    return persist({ view }, true);
  }
  return (
    <Context.Provider
      value={{
        view: normalizeFeedView(current?.value?.view),
        storedView: current?.value?.view ?? "cards",
        columns: current?.value?.columns ?? [],
        content_types: current?.value?.content_types ?? [...contentTypes],
        languages: current?.value?.languages ?? ["en"],
        // AccountGate hides personal content during session checks. Keep the
        // confirmed owner's layout mounted; a changed owner has no current state.
        loading: !current,
        busy: saving !== null || (!!current && current.requestKey !== requestKey),
        unavailable: current?.unavailable ?? false,
        error: failure?.owner === owner ? failure.message : "",
        save: (settings) => persist(settings),
        setView,
        setColumns: (columns) => persist({ columns }, true),
        refresh: () => setRevision((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
