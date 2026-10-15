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
export type ArticleOverlay = {
  slug: string;
  entry: "direct" | "intercepted";
  dismiss: "feed" | "history";
};
const Context = createContext<{
  motionRef: RefObject<{ target: string; direction: -1 | 1; at: number } | null>;
  sequence: ArticleSequence | null;
  setSequence: Dispatch<SetStateAction<ArticleSequence | null>>;
  overlay: ArticleOverlay | null;
}>({
  motionRef: { current: null },
  sequence: null,
  setSequence: () => {},
  overlay: null,
});
export const useArticleNavigation = () => useContext(Context);
export function ArticleNavigationProvider({
  children,
  overlay = null,
}: {
  children: React.ReactNode;
  overlay?: ArticleOverlay | null;
}) {
  const motionRef = useRef<{ target: string; direction: -1 | 1; at: number } | null>(null);
  const [sequence, setSequence] = useState<ArticleSequence | null>(null);
  return (
    <Context.Provider
      value={{
        motionRef,
        sequence,
        setSequence,
        overlay,
      }}
    >
      {children}
    </Context.Provider>
  );
}
