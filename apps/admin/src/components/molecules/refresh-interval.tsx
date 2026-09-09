"use client";

import { RefreshCw } from "lucide-react";
import { Select } from "./select";
import { cn } from "@/lib/utils";
import { refreshIntervals } from "@/lib/use-refresh-interval";

export function RefreshInterval({ value, onChange, loading = false, label = "Refresh interval" }: { value: number; onChange: (seconds: number) => void; loading?: boolean; label?: string }) {
  return <Select label={label} required size="sm" align="end" className="w-28" value={String(value)} onChange={value => onChange(Number(value))}
    aria-busy={loading} icon={<RefreshCw aria-hidden className={cn("size-3.5", loading && "animate-spin motion-reduce:animate-none")} />}
    options={refreshIntervals.map(seconds => ({ value: String(seconds), label: seconds === 0 ? "Off" : seconds === 60 ? "1 min" : `${seconds}s` }))} />;
}
