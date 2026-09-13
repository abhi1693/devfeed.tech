"use client";

import {
  createContext,
  useContext,
  useRef,
  useState,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";
export type ArticleSequence = {
  slugs: string[];
  hasMore: boolean;
  loading: boolean;
  loadMore: () => Promise<string | undefined>;
};
const Context = createContext<{
  motionRef: RefObject<{ target: string; direction: -1 | 1; at: number } | null>;
  sequence: ArticleSequence | null;
  setSequence: Dispatch<SetStateAction<ArticleSequence | null>>;
  hideDirect: boolean;
  setHideDirect: Dispatch<SetStateAction<boolean>>;
  directEntry: boolean;
  setDirectEntry: Dispatch<SetStateAction<boolean>>;
}>({
  motionRef: { current: null },
  sequence: null,
  setSequence: () => {},
  hideDirect: false,
  setHideDirect: () => {},
  directEntry: false,
  setDirectEntry: () => {},
});
export const useArticleNavigation = () => useContext(Context);
export function ArticleNavigationProvider({ children }: { children: React.ReactNode }) {
  const motionRef = useRef<{ target: string; direction: -1 | 1; at: number } | null>(null);
  const [sequence, setSequence] = useState<ArticleSequence | null>(null);
  const [hideDirect, setHideDirect] = useState(false);
  const [directEntry, setDirectEntry] = useState(false);
  return (
    <Context.Provider
      value={{
        motionRef,
        sequence,
        setSequence,
        directEntry,
        setDirectEntry,
        hideDirect,
        setHideDirect,
      }}
    >
      {children}
    </Context.Provider>
  );
}
