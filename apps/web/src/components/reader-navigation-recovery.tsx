"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { navigationStarted } from "@/lib/reader-navigation";
import { readerLocation, readerPublicOrigin, readerReload } from "@/lib/reader-runtime";

const recoveryDelay = 10_000;

/** A stalled client transition must not strand a reader in an old tab. */
export function ReaderNavigationRecovery() {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const [pending, setPending] = useState(false);
  const cancel = useRef<() => void>(() => {});
  const committed = useRef<(path: string) => void>(() => {});
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let target: string | undefined;
    const superseded = new Set<string>();
    const clear = () => {
      clearTimeout(timer);
      timer = undefined;
    };
    cancel.current = () => {
      clear();
      target = undefined;
      superseded.clear();
      setPending(false);
    };
    committed.current = (path) => {
      // An older request may finish after the reader chooses another page.
      // Redirects and the latest destination complete the current transition.
      if (!superseded.has(path)) cancel.current();
    };
    const start = (event: Event) => {
      const href = (event as CustomEvent<unknown>).detail;
      if (typeof href !== "string") return;
      const url = new URL(href, readerLocation());
      if (url.origin !== readerPublicOrigin()) return;
      clear();
      const next = url.pathname + url.search;
      const current = readerLocation();
      if (next === current.pathname + current.search) {
        cancel.current();
        return;
      }
      if (target) superseded.add(target);
      superseded.delete(next);
      if (superseded.size > 10) superseded.delete(superseded.values().next().value!);
      target = next;
      setPending(true);
      timer = setTimeout(() => {
        setPending(false);
        // Committed route changes cancel this timer. An optimistic URL alone
        // does not prove that the client rendered the requested page.
        readerReload(next + url.hash);
      }, recoveryDelay);
    };
    window.addEventListener(navigationStarted, start);
    window.addEventListener("pagehide", cancel.current);
    window.addEventListener("popstate", cancel.current);
    return () => {
      clear();
      window.removeEventListener(navigationStarted, start);
      window.removeEventListener("pagehide", cancel.current);
      window.removeEventListener("popstate", cancel.current);
    };
  }, []);
  useEffect(
    () => committed.current((pathname ?? "") + (search ? `?${search}` : "")),
    [pathname, search],
  );
  return pending ? (
    <div className="reader-navigation-progress" role="status" aria-live="polite">
      <span className="sr-only">Loading page…</span>
    </div>
  ) : null;
}
