import { Badge } from "@/components/atoms/badge";
import { BooleanIndicator } from "@/components/atoms/boolean-indicator";
import { humanize } from "@/lib/resources";

type Tone = "success" | "warning" | "info" | "danger" | "neutral";
const tones: Record<string, Tone> = {
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
export function StatusBadge({ value, label }: { value: unknown; label?: string }) {
  if (typeof value === "boolean") return <BooleanIndicator value={value} label={label} />;
  if (value === null || value === undefined || value === "")
    return <span className="text-muted-foreground">—</span>;
  const text = String(value).trim();
  const key = text.toLowerCase();
  const tone = Object.hasOwn(tones, key) ? tones[key] : "neutral";
  return <Badge variant={tone}>{label ?? humanize(text)}</Badge>;
}
