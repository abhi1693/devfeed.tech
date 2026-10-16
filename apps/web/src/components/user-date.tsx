"use client";

import { formatDate } from "@devfeed/ui/date-format";
import { useDateTimePreferences } from "./theme-preferences";

export function UserDate({ value, className }: { value: string; className?: string }) {
  const appearance = useDateTimePreferences();
  return (
    <time
      className={className}
      dateTime={value}
      title={formatDate(value, appearance)}
      suppressHydrationWarning
    >
      {formatDate(value, appearance, true)}
    </time>
  );
}
