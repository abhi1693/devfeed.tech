"use client";

import type { ComponentProps } from "react";
import { Checkbox as CheckboxPrimitive, RadioGroup as RadioPrimitive } from "radix-ui";
import { Check } from "lucide-react";

export const ChoiceGroup = RadioPrimitive.Root;
export const ChoiceItem = RadioPrimitive.Item;

export function Checkbox({
  className = "",
  ...props
}: ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root {...props} className={`shared-checkbox ${className}`}>
      <CheckboxPrimitive.Indicator>
        <Check size={12} strokeWidth={3} aria-hidden="true" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
