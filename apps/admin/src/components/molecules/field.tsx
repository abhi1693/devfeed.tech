"use client";

import { useId, useRef, useState, type AriaAttributes, type ReactNode } from "react";
import { CircleHelp } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { FieldDescription, FieldError, FieldLabel } from "@/components/atoms/field";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";
import { cn } from "@/lib/utils";

export type FieldControlProps = Pick<AriaAttributes, "aria-describedby" | "aria-invalid"> & {
  id: string;
  name?: string;
  required: boolean;
  disabled: boolean;
};

export type FieldProps = {
  id?: string;
  name?: string;
  label: string;
  required?: boolean;
  disabled?: boolean;
  subtext?: ReactNode;
  tooltip?: string;
  error?: string;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
};

function FieldHelp({ label, children }: { label: string; children: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <Tooltip open={open} onOpenChange={setOpen}>
    <TooltipTrigger asChild><Button ref={trigger} variant="ghost" size="icon-xs" aria-label={`About ${label}`} aria-expanded={open}
      className="size-4 rounded-sm text-muted-foreground"
      onPointerDown={event => event.preventDefault()}
      onClick={event => { event.preventDefault(); setOpen(previous => !previous); }}>
      <CircleHelp aria-hidden className="size-3.5" />
    </Button></TooltipTrigger>
    <TooltipContent onPointerDownOutside={event => {
      // Let the trigger toggle the tooltip once; a touch outside-dismiss followed
      // by its click would otherwise immediately reopen an already-open tooltip.
      if (trigger.current?.contains(event.detail.originalEvent.target as Node)) event.preventDefault();
    }}>{children}</TooltipContent>
  </Tooltip>;
}

/** Compose any control by spreading the supplied props onto its focusable element. */
export function Field({ id, name, label, required = false, disabled = false, subtext, tooltip, error, className, children }: FieldProps) {
  const generatedId = useId();
  const controlId = id ?? `field-${generatedId}`;
  const hasSubtext = subtext !== undefined && subtext !== null && subtext !== false && subtext !== "";
  const describedBy = [hasSubtext && `${controlId}-help`, tooltip && `${controlId}-tooltip`, error && `${controlId}-error`].filter(Boolean).join(" ") || undefined;
  const control: FieldControlProps = { id: controlId, name, required, disabled, "aria-invalid": !!error, "aria-describedby": describedBy };
  return <div data-slot="field" data-invalid={!!error} data-disabled={disabled} className={cn("group min-w-0 space-y-2", className)}>
    <div className="flex items-center gap-1.5">
      <FieldLabel htmlFor={controlId} required={required}>{label}</FieldLabel>
      {tooltip && <FieldHelp label={label}>{tooltip}</FieldHelp>}
      {tooltip && <span id={`${controlId}-tooltip`} className="sr-only">{tooltip}</span>}
    </div>
    {children(control)}
    {hasSubtext && <FieldDescription id={`${controlId}-help`}>{subtext}</FieldDescription>}
    <FieldError id={`${controlId}-error`}>{error}</FieldError>
  </div>;
}
