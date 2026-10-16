"use client";

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import {
  readerPromptPolicies,
  type ArticleIdentity,
  type ReaderPromptEligibility,
} from "@/lib/reader-context";

type ReaderState = {
  currentArticle: ArticleIdentity | null;
  promptEligibility: ReaderPromptEligibility;
};
type SurfaceState = {
  currentArticle?: ArticleIdentity | null;
  promptEligibility: ReaderPromptEligibility;
};
type SurfaceOptions = { active?: boolean; priority?: "page" | "foreground" };
type Registration = { state: ReaderState; priority: number };
const emptyState: ReaderState = {
  currentArticle: null,
  promptEligibility: readerPromptPolicies.quiet,
};
const Context = createContext<
  ReaderState & {
    register?: (state: ReaderState, priority: number) => () => void;
  }
>(emptyState);

/** Foreground previews own reader state while their underlying page stays mounted. */
export function ReaderContextProvider({ children }: { children: ReactNode }) {
  const [surfaces, setSurfaces] = useState<Registration[]>([]);
  const register = useCallback((state: ReaderState, priority: number) => {
    const surface = { state, priority };
    setSurfaces((current) => [...current, surface]);
    return () => setSurfaces((current) => current.filter((value) => value !== surface));
  }, []);
  const foreground = surfaces.reduce<Registration | undefined>(
    (selected, surface) =>
      !selected || surface.priority >= selected.priority ? surface : selected,
    undefined,
  );
  return (
    <Context.Provider value={{ ...(foreground?.state ?? emptyState), register }}>
      {children}
    </Context.Provider>
  );
}

export function useReaderContext(): ReaderState {
  return useContext(Context);
}

/** Register rendered identities and policies; cleanup releases only this surface's lease. */
export function useReaderSurface(
  { currentArticle, promptEligibility }: SurfaceState,
  { active = true, priority = "page" }: SurfaceOptions = {},
) {
  const { register } = useContext(Context);
  const id = currentArticle?.id;
  const slug = currentArticle?.slug;
  const { signup, mustReads } = promptEligibility;
  useLayoutEffect(() => {
    if (!active || !register) return;
    return register(
      {
        currentArticle: id && slug ? { id, slug } : null,
        promptEligibility: { signup, mustReads },
      },
      priority === "foreground" ? 1 : 0,
    );
  }, [register, active, priority, id, slug, signup, mustReads]);
}

/** Server-rendered shells publish page policy through this client boundary. */
export function ReaderSurface(props: SurfaceState & SurfaceOptions) {
  useReaderSurface(props, props);
  return null;
}
