"use client";

import { Badge } from "@/components/atoms/badge";
import { BooleanIndicator } from "@/components/atoms/boolean-indicator";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { humanize } from "@/lib/resources";
import { languageName } from "@/lib/languages";
import type { ColumnPresentation } from "@/lib/column-kinds";

export function EmptyCell() {
  return <span className="text-muted-foreground">—</span>;
}

export function TextCell({ value }: { value: unknown }) {
  return typeof value === "string" || typeof value === "number" ? <>{value}</> : <EmptyCell />;
}

export function NameCell({ value }: { value: unknown }) {
  return (
    <span className="font-medium">
      <TextCell value={value} />
    </span>
  );
}

export function SlugCell({ value }: { value: unknown }) {
  return typeof value === "string" && value ? (
    <code className="break-all text-xs">{value}</code>
  ) : (
    <EmptyCell />
  );
}

export function BooleanCell({ value }: { value: unknown }) {
  return typeof value === "boolean" ? <BooleanIndicator value={value} /> : <EmptyCell />;
}

export function TagsCell({ value }: { value: unknown }) {
  const labels = Array.isArray(value)
    ? value.flatMap((item: unknown) => {
        if (typeof item === "string") return item ? [item] : [];
        if (item && typeof item === "object" && "name" in item && typeof item.name === "string")
          return item.name ? [item.name] : [];
        return [];
      })
    : [];
  return labels.length ? (
    <span className="flex flex-wrap gap-1">
      {[...new Set(labels)].map((label) => (
        <Badge key={label} variant="secondary">
          {label}
        </Badge>
      ))}
    </span>
  ) : (
    <EmptyCell />
  );
}

export function PillCell({ value, tone }: { value: unknown } & Pick<ColumnPresentation, "tone">) {
  return <StatusBadge value={value} tone={tone} />;
}

export function DateTimeCell({ value }: { value: unknown }) {
  return typeof value === "string" || typeof value === "number" || value instanceof Date ? (
    <DateTime value={value} className="whitespace-nowrap text-xs" />
  ) : (
    <EmptyCell />
  );
}

/** Literal values stay literal. Formatting is selected by the column's explicit kind. */
export function ColumnValue({
  value,
  kind = "text",
  tone,
}: { value: unknown } & ColumnPresentation) {
  if (value == null || value === "") return <EmptyCell />;
  switch (kind) {
    case "name":
      return <NameCell value={value} />;
    case "slug":
      return <SlugCell value={value} />;
    case "boolean":
      return <BooleanCell value={value} />;
    case "tags":
      return <TagsCell value={value} />;
    case "pill":
      return <PillCell value={value} tone={tone} />;
    case "datetime":
      return <DateTimeCell value={value} />;
    case "label":
      return <TextCell value={typeof value === "string" ? humanize(value) : value} />;
    case "language":
      return <TextCell value={typeof value === "string" ? languageName(value) : null} />;
    case "number":
      return typeof value === "number" && Number.isFinite(value) ? (
        <span className="tabular-nums">{value.toLocaleString("en")}</span>
      ) : (
        <EmptyCell />
      );
    default:
      return <TextCell value={value} />;
  }
}
