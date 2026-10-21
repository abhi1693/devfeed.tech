"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { contentTypes } from "@/lib/feed-query";
import { userRequest, type FeedDisplay } from "@/lib/user";
import { saveFeedSettings } from "@/lib/user-mutations";
import { normalizeFeedView } from "@/lib/reader-layouts";
import { useUser } from "./user-account";

type State = { owner: string; value: FeedDisplay | null; unavailable: boolean };
const guestViewKey = "devfeed:feed-view:guest";
const Context = createContext<{
  view: FeedDisplay["view"];
  content_types: FeedDisplay["content_types"];
  languages: string[];
  loading: boolean;
  busy: boolean;
  unavailable: boolean;
  error: string;
  save: (settings: FeedDisplay) => Promise<boolean>;
  setView: (view: FeedDisplay["view"]) => Promise<boolean>;
  refresh: () => void;
}>({
  view: "cards" as FeedDisplay["view"],
  content_types: [...contentTypes],
  languages: ["en"],
  loading: true,
  busy: false,
  unavailable: false,
  error: "",
  save: async () => false,
  setView: async () => false,
  refresh: () => {},
});
export const useFeedPreferences = () => useContext(Context);

export function FeedPreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user, loading, sessionRevision } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<State | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState<string | null>(null);
  const savingRef = useRef(false);
  const account = useRef<AbortController | null>(null);
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null);
  useEffect(() => {
    if (loading) return;
    const controller = new AbortController();
    account.current = controller;
    async function load() {
      let value: FeedDisplay = {
        view: "cards",
        content_types: [...contentTypes],
        languages: ["en"],
      };
      if (owner !== "guest") {
        value = await userRequest<FeedDisplay>("settings/feed", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
      } else {
        try {
          value.view = normalizeFeedView(localStorage.getItem(guestViewKey));
        } catch {
          // Storage restrictions must not prevent reading or switching layouts.
        }
      }
      if (!controller.signal.aborted)
        setState({
          owner,
          value: { ...value, view: normalizeFeedView(value.view) },
          unavailable: false,
        });
    }
    void load().catch(() => {
      if (!controller.signal.aborted) setState({ owner, value: null, unavailable: true });
    });
    return () => controller.abort();
  }, [owner, loading, revision, sessionRevision]);
  const current = state?.owner === owner ? state : null;
  async function save(settings: FeedDisplay) {
    if (savingRef.current || loading || !user) return false;
    const controller = account.current;
    if (!controller || controller.signal.aborted) return false;
    savingRef.current = true;
    setSaving(owner);
    setFailure(null);
    try {
      const result = await saveFeedSettings(settings, {
        csrfToken: user.csrf_token,
        signal: controller.signal,
      });
      const value = { ...result, view: normalizeFeedView(result.view) };
      if (controller.signal.aborted) return false;
      setState((previous) =>
        previous?.owner === owner ? { owner, value, unavailable: false } : previous,
      );
      return true;
    } catch {
      if (!controller.signal.aborted)
        setFailure({ owner, message: "Couldn’t save your feed settings. Please try again." });
      return false;
    } finally {
      savingRef.current = false;
      setSaving(null);
    }
  }
  async function setView(view: FeedDisplay["view"]) {
    if (loading || !current?.value || current.unavailable || savingRef.current) return false;
    if (view === current.value.view) return false;
    const previous = current;
    const value: FeedDisplay = {
      view,
      content_types: current.value.content_types ?? [...contentTypes],
      languages: current.value.languages ?? ["en"],
    };
    setState({ owner, value, unavailable: false });
    if (!user) {
      try {
        localStorage.setItem(guestViewKey, view);
      } catch {
        // The selected layout still works for this tab when storage is blocked.
        return false;
      }
    } else if (!(await save(value))) {
      setState((latest) => (latest?.owner === owner && latest.value === value ? previous : latest));
      return false;
    }
    return true;
  }
  return (
    <Context.Provider
      value={{
        view: current?.value?.view ?? "cards",
        content_types: current?.value?.content_types ?? [...contentTypes],
        languages: current?.value?.languages ?? ["en"],
        // AccountGate hides personal content during session checks. Keep the
        // confirmed owner's layout mounted; a changed owner has no current state.
        loading: !current,
        busy: saving !== null,
        unavailable: current?.unavailable ?? false,
        error: failure?.owner === owner ? failure.message : "",
        save,
        setView,
        refresh: () => setRevision((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
