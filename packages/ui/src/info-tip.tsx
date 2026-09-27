"use client";

import { useState, type ReactNode } from "react";
import { Info } from "lucide-react";
import { Tooltip } from "radix-ui";

export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root open={open} onOpenChange={setOpen}>
        <Tooltip.Trigger asChild>
          <button
            type="button"
            className="shared-info-tip"
            aria-label={label}
            onClick={(event) => {
              // Keep the tooltip available to touch users as well as hover/focus.
              event.preventDefault();
              setOpen((value) => !value);
            }}
          >
            <Info size={14} aria-hidden="true" />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="shared-info-tip-content"
            side="top"
            sideOffset={6}
            collisionPadding={12}
          >
            {children}
            <Tooltip.Arrow className="shared-info-tip-arrow" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
