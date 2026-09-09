"use client";

import type { ReactNode } from "react";
import { Input } from "@/components/atoms/input";
import { Field } from "./field";

/** Shared explicit confirmation for individual and bulk deletion. */
export function DeleteConfirmation({ value, onChange, disabled = false, children }: {
  value: string; onChange: (value: string) => void; disabled?: boolean; children?: ReactNode;
}) {
  return <div className="space-y-5">
    {children}
    <Field label="Type DELETE to confirm" required disabled={disabled}>
      {control => <Input {...control} value={value} onChange={event => onChange(event.target.value)} autoComplete="off" />}
    </Field>
  </div>;
}
