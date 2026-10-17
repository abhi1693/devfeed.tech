"use client";

import { createContext, useContext } from "react";
type Preferences = { tag_ids: string[] };
import { useFollowPreferences } from "@/lib/use-follow-preferences";

const Context = createContext({
  ids: [] as string[],
  loading: true,
  unavailable: false,
  busy: [] as string[],
  errors: {} as Record<string, string>,
  messages: {} as Record<string, string>,
  refresh: () => {},
  toggle: async (_id: string) => {
    void _id;
  },
  save: async (_ids: string[]): Promise<Preferences | null> => {
    void _ids;
    return null;
  },
});
export const useTagFollows = () => useContext(Context);

export function TagFollowsProvider({ children }: { children: React.ReactNode }) {
  const follows = useFollowPreferences("tag");
  return (
    <Context.Provider
      value={{
        ...follows,
        save: async (ids) => {
          const result = await follows.save(ids);
          return result ? { tag_ids: result } : null;
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
