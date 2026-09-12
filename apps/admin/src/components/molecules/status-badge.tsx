import { Badge } from "@/components/atoms/badge";
import { BooleanIndicator } from "@/components/atoms/boolean-indicator";
import { humanize } from "@/lib/resources";
import type { PillTone } from "@/lib/column-kinds";

const tones: Record<string, PillTone> = {
  ready: "success",
  refreshing: "info",
  expired: "warning",
  approved: "success",
  published: "success",
  active: "success",
  succeeded: "success",
  enabled: "success",
  approve: "success",
  publish: "success",
  success: "success",
  pending: "warning",
  proposed: "warning",
  queued: "warning",
  warning: "warning",
  warn: "warning",
  running: "info",
  info: "info",
  busy: "info",
  idle: "success",
  suspended: "warning",
  offline: "danger",
  rejected: "danger",
  failed: "danger",
  reject: "danger",
  error: "danger",
  critical: "danger",
  unpublished: "neutral",
  disabled: "neutral",
  unpublish: "neutral",
  debug: "neutral",
};

/** Text states keep labels; boolean states use accessible check/cross icons. */
export function StatusBadge({
  value,
  label,
  tone,
}: {
  value: unknown;
  label?: string;
  tone?: PillTone;
}) {
  if (typeof value === "boolean") return <BooleanIndicator value={value} label={label} />;
  if (value === null || value === undefined || value === "")
    return <span className="text-muted-foreground">—</span>;
  const text = String(value).trim();
  const key = text.toLowerCase();
  const variant = tone ?? (Object.hasOwn(tones, key) ? tones[key] : "neutral");
  return <Badge variant={variant}>{label ?? humanize(text)}</Badge>;
}
