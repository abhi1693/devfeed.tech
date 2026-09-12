"use client";

import { useSettings } from "@/lib/use-settings";
import { formatDate } from "@/lib/settings";

export function DateTime({
  value,
  dateOnly = false,
  className,
}: {
  value: string | number | Date;
  dateOnly?: boolean;
  className?: string;
}) {
  const { settings } = useSettings();
  const date = new Date(value);
  return (
    <time
      className={className}
      dateTime={Number.isFinite(date.getTime()) ? date.toISOString() : undefined}
      title={formatDate(value, settings.appearance)}
      suppressHydrationWarning
    >
      {formatDate(value, settings.appearance, dateOnly)}
    </time>
  );
}
