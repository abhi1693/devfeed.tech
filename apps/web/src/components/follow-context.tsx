"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useFollowPreferences } from "@/lib/use-follow-preferences";

/** Keep each collection isolated while sharing its provider and preference adapter. */
export function createFollowContext<Key extends "tag_ids" | "topic_ids">(
  kind: "tag" | "topic",
  field: Key,
) {
  type Preferences = Record<Key, string[]>;
  type State = Omit<ReturnType<typeof useFollowPreferences>, "save"> & {
    save: (ids: string[]) => Promise<Preferences | null>;
  };
  const Context = createContext<State>({
    ids: [],
    loading: true,
    unavailable: false,
    busy: [],
    errors: {},
    messages: {},
    refresh: () => undefined,
    toggle: async () => undefined,
    save: async () => null,
  });
  function Provider({ children }: { children: ReactNode }) {
    const follows = useFollowPreferences(kind);
    return (
      <Context.Provider
        value={{
          ...follows,
          save: async (ids) => {
            const result = await follows.save(ids);
            return result ? ({ [field]: result } as Preferences) : null;
          },
        }}
      >
        {children}
      </Context.Provider>
    );
  }
  return { Provider, useFollows: () => useContext(Context) };
}
