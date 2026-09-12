"use client";

import { createContext, useContext, useState, type Dispatch, type SetStateAction } from "react";
export type ArticleSequence = {
  slugs: string[];
  hasMore: boolean;
  loading: boolean;
  loadMore: () => Promise<string | undefined>;
};
const Context = createContext<{
  sequence: ArticleSequence | null;
  setSequence: Dispatch<SetStateAction<ArticleSequence | null>>;
  hideDirect: boolean;
  setHideDirect: Dispatch<SetStateAction<boolean>>;
  directEntry: boolean;
  setDirectEntry: Dispatch<SetStateAction<boolean>>;
}>({
  sequence: null,
  setSequence: () => {},
  hideDirect: false,
  setHideDirect: () => {},
  directEntry: false,
  setDirectEntry: () => {},
});
export const useArticleNavigation = () => useContext(Context);
export function ArticleNavigationProvider({ children }: { children: React.ReactNode }) {
  const [sequence, setSequence] = useState<ArticleSequence | null>(null);
  const [hideDirect, setHideDirect] = useState(false);
  const [directEntry, setDirectEntry] = useState(false);
  return (
    <Context.Provider
      value={{ sequence, setSequence, directEntry, setDirectEntry, hideDirect, setHideDirect }}
    >
      {children}
    </Context.Provider>
  );
}
