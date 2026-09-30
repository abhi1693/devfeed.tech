"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";

export const navigationStarted = "devfeed:reader-navigation";

export function beginReaderNavigation(href: string) {
  window.dispatchEvent(new CustomEvent(navigationStarted, { detail: href }));
}

/** Programmatic navigation uses the same recovery path as reader links. */
export function useReaderRouter() {
  const router = useRouter();
  return useMemo(
    () => ({
      ...router,
      push: (...args: Parameters<typeof router.push>) => {
        beginReaderNavigation(args[0]);
        router.push(...args);
      },
      replace: (...args: Parameters<typeof router.replace>) => {
        beginReaderNavigation(args[0]);
        router.replace(...args);
      },
    }),
    [router],
  );
}
