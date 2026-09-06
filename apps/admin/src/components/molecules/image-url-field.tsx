"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { ImageIcon, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { UrlInput, type UrlInputProps } from "@/components/atoms/url-input";
import { imagePreviewUrl } from "@/lib/image-preview";
import { cn } from "@/lib/utils";
import { ImagePreview } from "./image-preview";

export function ImageUrlField({ value, className, disabled, onChange, ...props }: UrlInputProps) {
  const url = imagePreviewUrl(value);
  const [mode, setMode] = useState<"hover" | "explicit" | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const cancel = () => clearTimeout(timer.current);
  useEffect(() => () => clearTimeout(timer.current), [url, disabled]);
  const open = !!mode && !!url && !disabled;
  function enter(event: PointerEvent) {
    if (event.pointerType === "touch") return;
    cancel();
    if (!mode && url && !disabled) timer.current = setTimeout(() => setMode("hover"), 250);
  }
  function leave(event: PointerEvent) {
    if (event.pointerType === "touch") return;
    cancel();
    if (mode === "hover") timer.current = setTimeout(() => setMode(null), 200);
  }
  function close() { cancel(); setMode(null); }
  return <Popover open={open} onOpenChange={next => { cancel(); setMode(next ? "explicit" : null); }}>
    <PopoverAnchor asChild><div className="relative" onPointerEnter={enter} onPointerLeave={leave}>
      <UrlInput {...props} value={value} disabled={disabled} className={cn("pr-10", className)} onChange={event => { close(); onChange?.(event); }} />
      <PopoverTrigger asChild><Button ref={trigger} variant="ghost" size="icon" disabled={disabled || !url} aria-label="Preview image" title="Preview image (hover or click)"
        className="absolute inset-y-0 right-0 rounded-l-none text-muted-foreground">
        <ImageIcon aria-hidden className="size-4" />
      </Button></PopoverTrigger>
    </div></PopoverAnchor>
    {url && <PopoverContent ref={content} aria-label="Image preview" className="w-80 max-w-[calc(100vw-1.5rem)] p-2" onPointerEnter={enter} onPointerLeave={leave}
      onEscapeKeyDown={() => { if (content.current?.contains(document.activeElement)) trigger.current?.focus(); }}
      onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}>
      <div className="mb-2 flex items-center justify-between gap-2 pl-1 text-xs font-medium">
        Image preview
        <Button variant="ghost" size="icon-xs" aria-label="Close image preview" onClick={() => { close(); trigger.current?.focus(); }} className="text-muted-foreground"><X aria-hidden className="size-3.5" /></Button>
      </div>
      <ImagePreview key={url} src={url} />
    </PopoverContent>}
  </Popover>;
}
