"use client";

import { formatDate } from "@devfeed/ui/date-format";
import { useDateTimePreferences } from "./theme-preferences";

export function UserDate({ value }: { value: string }) {
  const appearance = useDateTimePreferences();
  return <time dateTime={value} title={formatDate(value, appearance)} suppressHydrationWarning>{formatDate(value, appearance, true)}</time>;
}
