import { Check, X } from "lucide-react";

/** Read-only boolean value, not an interactive toggle. */
export function BooleanIndicator({ value, label = value ? "Yes" : "No" }: { value: boolean; label?: string }) {
  const Icon = value ? Check : X;
  return <span role="img" aria-label={label} title={label} className="inline-flex align-middle">
    <Icon aria-hidden className={`size-4 ${value ? "text-emerald-700" : "text-rose-700"}`} strokeWidth={2.5} />
  </span>;
}
