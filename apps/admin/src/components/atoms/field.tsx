import type { ComponentProps } from "react";
import { Label } from "./label";
import { cn } from "@/lib/utils";

export function FieldLabel({ required, children, ...props }: ComponentProps<typeof Label> & { required?: boolean }) {
  return <Label {...props}>{children}{required && <span aria-hidden className="text-destructive">*</span>}</Label>;
}

export function FieldDescription({ className, ...props }: ComponentProps<"p">) {
  return <p data-slot="field-description" className={cn("text-xs leading-relaxed text-muted-foreground", className)} {...props} />;
}

export function FieldError({ className, children, ...props }: ComponentProps<"p">) {
  if (!children) return null;
  return <p data-slot="field-error" role="alert" className={cn("text-xs leading-relaxed text-destructive", className)} {...props}>{children}</p>;
}
