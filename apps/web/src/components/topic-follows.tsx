"use client";

import { createContext, useContext } from "react";
import type { Preferences } from "@/lib/user";
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
export const useTopicFollows = () => useContext(Context);

export function TopicFollowsProvider({ children }: { children: React.ReactNode }) {
  const follows = useFollowPreferences("topic");
  return (
    <Context.Provider
      value={{
        ...follows,
        save: async (ids) => {
          const result = await follows.save(ids);
          return result ? { topic_ids: result } : null;
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
