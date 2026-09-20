"use client";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { AccountError, userRequest } from "@/lib/user";
import { useUser } from "./user-account";
import { FollowButton } from "./follow-button";

type Preferences = { source_ids: string[] };
const Context = createContext({
  ids: [] as string[],
  loading: true,
  unavailable: false,
  busy: [] as string[],
  error: "",
  refresh: () => {},
  toggle: async (_id: string) => {
    void _id;
  },
  save: async (_ids: string[]) => {
    void _ids;
    return false;
  },
});
export const useSourceFollows = () => useContext(Context);
export function SourceFollowsProvider({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const owner = user?.user_id ?? "guest";
  const [state, setState] = useState<{ owner: string; ids: string[]; unavailable: boolean } | null>(
    null,
  );
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);
  const mutations = useRef(new Set<string>());
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    request.current = controller;
    userRequest<Preferences>("preferences/sources", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted)
          setState({ owner, ids: value.source_ids, unavailable: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ owner, ids: [], unavailable: true });
      });
    return () => controller.abort();
  }, [user, owner, revision]);
  const current = state?.owner === owner ? state : null;
  async function mutate(ids: string[], id?: string) {
    if (
      !user ||
      !current ||
      current.unavailable ||
      mutations.current.has(id ?? "all") ||
      mutations.current.has("all") ||
      (!id && mutations.current.size)
    )
      return false;
    const token = id ?? "all";
    const controller = request.current;
    if (!controller || controller.signal.aborted) return false;
    mutations.current.add(token);
    setBusy([...mutations.current]);
    setError("");
    try {
      const followed = id ? !current.ids.includes(id) : false;
      const result = await userRequest<Preferences | { followed: boolean }>(
        `preferences/sources${id ? `/${id}` : ""}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify(id ? { followed } : { source_ids: ids }),
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        },
      );
      if (controller.signal.aborted) return false;
      setState((previous) => ({
        owner,
        unavailable: false,
        ids:
          "source_ids" in result
            ? result.source_ids
            : result.followed
              ? [...new Set([...(previous?.owner === owner ? previous.ids : []), id!])]
              : (previous?.ids ?? []).filter((value) => value !== id),
      }));
      return true;
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof AccountError && cause.status === 422
            ? "Choose up to 100 approved sources."
            : "Couldn’t update your sources. Please try again.",
        );
      return false;
    } finally {
      mutations.current.delete(token);
      setBusy([...mutations.current]);
    }
  }
  return (
    <Context.Provider
      value={{
        ids: current?.ids ?? [],
        loading: loading || (!!user && !current),
        unavailable: !!current?.unavailable,
        busy,
        error,
        refresh: () => setRevision((value) => value + 1),
        toggle: async (id) => {
          await mutate([], id);
        },
        save: (ids) => mutate(ids),
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function SourceFollow({
  sourceId,
  returnTo,
  compact = false,
}: {
  sourceId: string;
  returnTo: string;
  compact?: boolean;
}) {
  const { user } = useUser();
  const { ids, loading, unavailable, busy, error, toggle, refresh } = useSourceFollows();
  const followed = ids.includes(sourceId),
    pending = busy.includes(sourceId) || busy.includes("all");
  return (
    <div className="topic-follow source-follow">
      <FollowButton
        signedIn={!!user}
        loading={loading}
        followed={followed}
        pending={pending}
        disabled={loading || pending || unavailable}
        returnTo={returnTo}
        onClick={() => void toggle(sourceId)}
        labels={
          compact
            ? undefined
            : {
                follow: "Follow source",
                following: "Following source",
              }
        }
      />
      {unavailable && (
        <button className="settings-button" onClick={refresh}>
          Retry source preferences
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
