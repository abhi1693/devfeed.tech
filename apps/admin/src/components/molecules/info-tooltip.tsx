"use client";

import { useRef, useState, type ReactNode } from "react";
import { Info } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";

export function InfoTooltip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <Tooltip open={open} onOpenChange={setOpen}>
    <TooltipTrigger asChild><Button ref={trigger} variant="ghost" size="icon-xs" aria-label={`About ${label}`} aria-expanded={open}
      className="size-4 rounded-sm text-muted-foreground"
      onPointerDown={event => event.preventDefault()}
      onClick={event => { event.preventDefault(); setOpen(previous => !previous); }}>
      <Info aria-hidden className="size-3.5" />
    </Button></TooltipTrigger>
    <TooltipContent onPointerDownOutside={event => {
      // Let the trigger toggle the tooltip once; a touch outside-dismiss followed
      // by its click would otherwise immediately reopen an already-open tooltip.
      if (trigger.current?.contains(event.detail.originalEvent.target as Node)) event.preventDefault();
    }}>{children}</TooltipContent>
  </Tooltip>;
}
