"use client";

import type { ReactNode } from "react";
import { readerReload } from "@/lib/reader-runtime";
import Link from "next/link";

export function ReaderReloadLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      className="button"
      href={href}
      prefetch={false}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
          return;
        event.preventDefault();
        readerReload(href);
      }}
    >
      {children}
    </Link>
  );
}
