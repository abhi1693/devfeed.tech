"use client";

import type { ComponentProps } from "react";
import { Command as CommandPrimitive } from "cmdk";
import { SearchIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function Command({ className, ...props }: ComponentProps<typeof CommandPrimitive>) {
  return <CommandPrimitive data-slot="command" className={cn("flex w-full flex-col overflow-hidden rounded-sm bg-popover text-popover-foreground", className)} {...props} />;
}
export function CommandInput({ className, ...props }: ComponentProps<typeof CommandPrimitive.Input>) {
  return <div className="mx-2 flex items-center gap-2 border-b px-1" data-slot="command-input-wrapper">
    <SearchIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
    <CommandPrimitive.Input data-slot="command-input" className={cn("h-10 w-full min-w-0 bg-transparent text-base outline-none placeholder:text-muted-foreground disabled:opacity-50 sm:text-sm", className)} {...props} />
  </div>;
}
export function CommandList({ className, ...props }: ComponentProps<typeof CommandPrimitive.List>) {
  return <CommandPrimitive.List data-slot="command-list" className={cn("max-h-64 scroll-py-1 overflow-x-hidden overflow-y-auto overscroll-contain p-1", className)} {...props} />;
}
export function CommandEmpty(props: ComponentProps<typeof CommandPrimitive.Empty>) {
  return <CommandPrimitive.Empty data-slot="command-empty" className="px-3 py-8 text-center text-sm text-muted-foreground" {...props} />;
}
export function CommandGroup({ className, ...props }: ComponentProps<typeof CommandPrimitive.Group>) {
  return <CommandPrimitive.Group data-slot="command-group" className={cn("[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground", className)} {...props} />;
}
export function CommandItem({ className, ...props }: ComponentProps<typeof CommandPrimitive.Item>) {
  return <CommandPrimitive.Item data-slot="command-item" className={cn("relative flex cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm outline-none select-none data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50", className)} {...props} />;
}
