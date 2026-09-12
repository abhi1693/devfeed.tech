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
  orientation?: "vertical" | "horizontal";
  children: (control: FieldControlProps) => ReactNode;
};

function FieldHelp({ label, children }: { label: string; children: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <Button
          ref={trigger}
          variant="ghost"
          size="icon-xs"
          aria-label={`About ${label}`}
          aria-expanded={open}
          className="size-4 rounded-sm text-muted-foreground"
          onPointerDown={(event) => event.preventDefault()}
          onClick={(event) => {
            event.preventDefault();
            setOpen((previous) => !previous);
          }}
        >
          <CircleHelp aria-hidden className="size-3.5" />
        </Button>
      </TooltipTrigger>
      <TooltipContent
        onPointerDownOutside={(event) => {
          // Let the trigger toggle the tooltip once; a touch outside-dismiss followed
          // by its click would otherwise immediately reopen an already-open tooltip.
          if (trigger.current?.contains(event.detail.originalEvent.target as Node))
            event.preventDefault();
        }}
      >
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

/** Compose any control by spreading the supplied props onto its focusable element. */
export function Field({
  id,
  name,
  label,
  required = false,
  disabled = false,
  subtext,
  tooltip,
  error,
  className,
  orientation = "vertical",
  children,
}: FieldProps) {
  const horizontal = orientation === "horizontal";
  const generatedId = useId();
  const controlId = id ?? `field-${generatedId}`;
  const hasSubtext =
    subtext !== undefined && subtext !== null && subtext !== false && subtext !== "";
  const describedBy =
    [
      hasSubtext && `${controlId}-help`,
      tooltip && `${controlId}-tooltip`,
      error && `${controlId}-error`,
    ]
      .filter(Boolean)
      .join(" ") || undefined;
  const control: FieldControlProps = {
    id: controlId,
    name,
    required,
    disabled,
    "aria-invalid": !!error,
    "aria-describedby": describedBy,
  };
  return (
    <div
      data-slot="field"
      data-invalid={!!error}
      data-disabled={disabled}
      className={cn(
        "group min-w-0 space-y-2",
        horizontal &&
          "sm:grid sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-x-6 sm:gap-y-1 sm:space-y-0",
        className,
      )}
    >
      <div
        className={cn(
          "flex items-center gap-1.5",
          horizontal && "sm:col-start-1 sm:row-start-1 sm:self-start sm:pt-2",
        )}
      >
        <FieldLabel htmlFor={controlId} required={required}>
          {label}
        </FieldLabel>
        {tooltip && <FieldHelp label={label}>{tooltip}</FieldHelp>}
        {tooltip && (
          <span id={`${controlId}-tooltip`} className="sr-only">
            {tooltip}
          </span>
        )}
      </div>
      {horizontal ? (
        <div className="min-w-0 sm:col-start-2 sm:row-start-1">{children(control)}</div>
      ) : (
        children(control)
      )}
      {hasSubtext && (
        <FieldDescription
          className={horizontal ? "sm:col-start-2" : undefined}
          id={`${controlId}-help`}
        >
          {subtext}
        </FieldDescription>
      )}
      <FieldError className={horizontal ? "sm:col-start-2" : undefined} id={`${controlId}-error`}>
        {error}
      </FieldError>
    </div>
  );
}
