"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Select } from "./select";
import { cn } from "@/lib/utils";
import { refreshIntervals } from "@/lib/use-refresh-interval";

export function RefreshInterval({ value, onChange, loading = false, label = "Refresh interval" }: { value: number; onChange: (seconds: number) => void; loading?: boolean; label?: string }) {
  const [activity, setActivity] = useState({ loading, finishing: false });
  // Keep fast refreshes visible without holding up data or the interval menu.
  if (activity.loading !== loading) setActivity({ loading, finishing: !loading });
  useEffect(() => {
    if (!activity.finishing) return;
    const timer = setTimeout(() => setActivity(current => ({ ...current, finishing: false })), 700);
    return () => clearTimeout(timer);
  }, [activity.finishing]);
  const spinning = loading || (value > 0 && activity.finishing);
  return <Select label={label} required size="sm" align="end" className="w-28" value={String(value)} onChange={value => onChange(Number(value))}
    aria-busy={loading} icon={<RefreshCw aria-hidden className={cn("size-3.5", spinning && "animate-spin [animation-duration:700ms] motion-reduce:animate-none")} />}
    options={refreshIntervals.map(seconds => ({ value: String(seconds), label: seconds === 0 ? "Off" : seconds === 60 ? "1 min" : `${seconds}s` }))} />;
}
