"use client";

import type { ComponentProps } from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

export function Tooltip(props: ComponentProps<typeof TooltipPrimitive.Root>) {
  return <TooltipPrimitive.Provider delayDuration={350}><TooltipPrimitive.Root {...props} /></TooltipPrimitive.Provider>;
}

export const TooltipTrigger = TooltipPrimitive.Trigger;

export function TooltipContent({ className, sideOffset = 6, ...props }: ComponentProps<typeof TooltipPrimitive.Content>) {
  return <TooltipPrimitive.Portal><TooltipPrimitive.Content data-slot="tooltip-content" sideOffset={sideOffset} collisionPadding={12}
    className={cn("z-50 max-w-[min(20rem,calc(100vw-1.5rem))] rounded-md border bg-popover px-3 py-2 text-xs leading-relaxed text-popover-foreground shadow-md [overflow-wrap:anywhere] data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 motion-reduce:animate-none", className)} {...props} />
  </TooltipPrimitive.Portal>;
}
