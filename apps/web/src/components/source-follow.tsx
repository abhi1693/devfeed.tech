"use client";
import { createContext, useContext } from "react";
import { useFollowPreferences } from "@/lib/use-follow-preferences";
import { useUser } from "./user-account";
import { FollowButton } from "./follow-button";

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
  const follows = useFollowPreferences("source");
  return (
    <Context.Provider
      value={{
        ...follows,
        error: Object.values(follows.errors).find(Boolean) ?? "",
        save: async (ids) => (await follows.save(ids)) !== null,
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
