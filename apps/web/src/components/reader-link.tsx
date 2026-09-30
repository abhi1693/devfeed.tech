"use client";

import NextLink from "next/link";
import { useRef, type ComponentProps } from "react";
import { beginReaderNavigation } from "@/lib/reader-navigation";

/** Reader destinations load on navigation, never just because a link is visible. */
export default function ReaderLink(props: ComponentProps<typeof NextLink>) {
  const destination = useRef("");
  return (
    <NextLink
      {...props}
      prefetch={false}
      onClick={(event) => {
        destination.current = event.currentTarget.href;
        props.onClick?.(event);
      }}
      onNavigate={(event) => {
        let cancelled = false;
        props.onNavigate?.({
          preventDefault: () => {
            cancelled = true;
            event.preventDefault();
          },
        });
        if (!cancelled)
          beginReaderNavigation(typeof props.href === "string" ? props.href : destination.current);
      }}
    />
  );
}
