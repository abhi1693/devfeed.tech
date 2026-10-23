"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { resolveReaderDestination, type ReaderDestination } from "./reader-destination";
import { readerLocation, readerNavigate } from "./reader-runtime";
import { cancelDialogScrollPreparation, prepareDialogScroll } from "./dialog-scroll";

export const navigationStarted = "devfeed:reader-navigation";

export function beginReaderNavigation(href: string) {
  cancelDialogScrollPreparation();
  if (!href.startsWith("#")) {
    const destination = resolveReaderDestination(href, { base: readerLocation() });
    if (destination.kind === "article") prepareDialogScroll(destination.slug);
  }
  window.dispatchEvent(new CustomEvent(navigationStarted, { detail: href }));
}

/** Programmatic navigation uses the same recovery path as reader links. */
export function useReaderRouter() {
  const router = useRouter();
  type Options = Parameters<typeof router.push>[1];
  return useMemo(() => {
    const navigate = (
      mode: "push" | "replace",
      value: string | ReaderDestination,
      options?: Options,
    ) => {
      const destination =
        typeof value === "string"
          ? resolveReaderDestination(value, {
              base: readerLocation(),
              scroll: options?.scroll === false ? "preserve" : "reset",
            })
          : value;
      beginReaderNavigation(destination.href);
      if (readerNavigate(destination, mode)) return;
      if (typeof value !== "string")
        router[mode](destination.href, { ...options, scroll: destination.scroll === "reset" });
      else if (options) router[mode](destination.href, options);
      else router[mode](destination.href);
    };
    return {
      ...router,
      push: (value: string | ReaderDestination, options?: Options) =>
        navigate("push", value, options),
      replace: (value: string | ReaderDestination, options?: Options) =>
        navigate("replace", value, options),
    };
  }, [router]);
}
