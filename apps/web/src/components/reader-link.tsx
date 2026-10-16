"use client";

import NextLink from "next/link";
import { useRef, type ComponentProps } from "react";
import { beginReaderNavigation } from "@/lib/reader-navigation";
import type { ReaderDestination } from "@/lib/reader-destination";
import { readerNavigate } from "@/lib/reader-runtime";

type ReaderLinkProps = Omit<ComponentProps<typeof NextLink>, "href"> & {
  href: ComponentProps<typeof NextLink>["href"] | ReaderDestination;
};

/** Reader destinations load on navigation, never just because a link is visible. */
export default function ReaderLink(props: ReaderLinkProps) {
  const destination = useRef("");
  const target = typeof props.href === "object" && "kind" in props.href ? props.href : undefined;
  const href = target ? target.href : (props.href as ComponentProps<typeof NextLink>["href"]);
  return (
    <NextLink
      {...props}
      href={href}
      scroll={target ? target.scroll === "reset" : props.scroll}
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
        if (cancelled) return;
        if (target) {
          beginReaderNavigation(target.href);
          if (readerNavigate(target, props.replace ? "replace" : "push")) event.preventDefault();
        } else beginReaderNavigation(typeof href === "string" ? href : destination.current);
      }}
    />
  );
}
