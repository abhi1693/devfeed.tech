"use client";

import Link from "next/link";
import { Tooltip } from "radix-ui";
import { useRef, useState, type ComponentProps } from "react";

export function TruncatedLink({
  children,
  ...props
}: Omit<ComponentProps<typeof Link>, "children" | "ref"> & { children: string }) {
  const link = useRef<HTMLAnchorElement>(null);
  const [open, setOpen] = useState(false);

  function changeOpen(next: boolean) {
    const element = link.current;
    // Measure on interaction, including after a resize, without an observer per card.
    setOpen(
      next &&
        !!element &&
        (element.scrollHeight > element.clientHeight || element.scrollWidth > element.clientWidth),
    );
  }

  return (
    <Tooltip.Provider delayDuration={350}>
      <Tooltip.Root open={open} onOpenChange={changeOpen}>
        <Tooltip.Trigger asChild>
          <Link {...props} ref={link}>
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
