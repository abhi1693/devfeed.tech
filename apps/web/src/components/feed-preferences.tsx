"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { contentTypes } from "@/lib/feed-query";
import { userRequest } from "@/lib/user";
import { useUser } from "./user-account";

export type FeedDisplay = {
  view: "cards" | "compact";
  content_types: (typeof contentTypes)[number][];
};
type State = { owner: string; value: FeedDisplay | null; unavailable: boolean };
const Context = createContext<{
  view: FeedDisplay["view"];
  content_types: FeedDisplay["content_types"];
  loading: boolean;
  busy: boolean;
  unavailable: boolean;
  error: string;
  save: (settings: FeedDisplay) => Promise<boolean>;
  refresh: () => void;
}>({
  view: "cards" as FeedDisplay["view"],
  content_types: [...contentTypes],
  loading: true,
  busy: false,
  unavailable: false,
  error: "",
  save: async () => false,
  refresh: () => {},
});
export const useFeedPreferences = () => useContext(Context);

export function FeedPreferencesProvider({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<State | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ owner: string; message: string } | null>(null);
  useEffect(() => {
    if (loading) return;
    const controller = new AbortController();
    async function load() {
      let value: FeedDisplay = { view: "cards", content_types: [...contentTypes] };
      if (owner !== "guest") {
        value = await userRequest<FeedDisplay>("settings/feed", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
      }
      if (!controller.signal.aborted) setState({ owner, value, unavailable: false });
    }
    void load().catch(() => {
      if (!controller.signal.aborted) setState({ owner, value: null, unavailable: true });
    });
    return () => controller.abort();
  }, [owner, loading, revision]);
  async function save(settings: FeedDisplay) {
    if (saving || loading || !user) return false;
    setSaving(owner);
    setFailure(null);
    try {
      const value = await userRequest<FeedDisplay>("settings/feed", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify(settings),
      });
      setState({ owner, value, unavailable: false });
      return true;
    } catch {
      setFailure({ owner, message: "Couldn’t save your feed settings. Please try again." });
      return false;
    } finally {
      setSaving(null);
    }
  }
  const current = state?.owner === owner ? state : null;
  return (
    <Context.Provider
      value={{
        view: current?.value?.view ?? "cards",
        content_types: current?.value?.content_types ?? [...contentTypes],
        loading: loading || !current,
        busy: saving === owner,
        unavailable: current?.unavailable ?? false,
        error: failure?.owner === owner ? failure.message : "",
        save,
        refresh: () => setRevision((value) => value + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}
