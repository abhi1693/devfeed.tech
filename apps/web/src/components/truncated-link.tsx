"use client";

import Link from "@/components/reader-link";
import { Tooltip } from "radix-ui";
import { useRef, useState, type ComponentProps, type ReactNode } from "react";

export function TruncatedLink({
  children,
  onClick,
  ...props
}: Omit<ComponentProps<typeof Link>, "children" | "ref"> & { children: ReactNode }) {
  const link = useRef<HTMLAnchorElement>(null);
  const [open, setOpen] = useState(false);

  function changeOpen(next: boolean) {
    const element = link.current;
    const modal = element?.ownerDocument.querySelector("dialog:modal");
    // Measure on interaction, including after a resize, without an observer per card.
    setOpen(
      next &&
        !!element &&
        (!modal || modal.contains(element)) &&
        (element.scrollHeight > element.clientHeight || element.scrollWidth > element.clientWidth),
    );
  }

  return (
    <Tooltip.Provider delayDuration={350}>
      <Tooltip.Root open={open} onOpenChange={changeOpen}>
        <Tooltip.Trigger asChild>
          <Link
            {...props}
            ref={link}
            onClick={(event) => {
              // A retained feed stays mounted behind previews; its tooltip must
              // release Escape before navigation opens a native modal.
              setOpen(false);
              onClick?.(event);
            }}
          >
            {children}
          </Link>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="truncated-text-tooltip"
            side="top"
            sideOffset={8}
            collisionPadding={12}
          >
            {children}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
