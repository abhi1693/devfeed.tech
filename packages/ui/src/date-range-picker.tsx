"use client";

import { useId, useState } from "react";
import { Popover } from "radix-ui";
import { CalendarDays, Check, ChevronDown } from "lucide-react";
import { Button } from "./button";
import { DatePicker } from "./date-picker";

const DAY = 86_400_000;
const iso = (date: Date) => date.toISOString().slice(0, 10);
function parseDate(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(date.getTime()) && iso(date) === value
    ? date
    : null;
}
function addDays(date: Date, days: number) {
  return new Date(date.getTime() + days * DAY);
}
function addMonths(date: Date, months: number) {
  const next = new Date(date);
  const day = next.getUTCDate();
  next.setUTCDate(1);
  next.setUTCMonth(next.getUTCMonth() + months);
  const last = new Date(next);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  next.setUTCDate(Math.min(day, last.getUTCDate()));
  return next;
}

export type DateRange = { from?: string; to?: string };
export const defaultDateRange: DateRange = { from: "now-29d/d", to: "now" };

function dateMath(value: string, now: Date, end: boolean) {
  const absolute = parseDate(value);
  if (absolute) return absolute;
  if (value.length > 64) return null;
  const match = /^now((?:[+-]\d+[dwMy])*)(?:\/([dwMy]))?$/.exec(value);
  if (!match) return null;
  let date = parseDate(iso(now))!;
  for (const operation of match[1].matchAll(/([+-])(\d+)([dwMy])/g)) {
    const count = Number(operation[2]) * (operation[1] === "-" ? -1 : 1);
    if (Math.abs(count) > 100_000) return null;
    date =
      operation[3] === "M" || operation[3] === "y"
        ? addMonths(date, count * (operation[3] === "y" ? 12 : 1))
        : addDays(date, count * (operation[3] === "w" ? 7 : 1));
    if (
      !Number.isFinite(date.getTime()) ||
      date.getUTCFullYear() < 1 ||
      date.getUTCFullYear() > 9999
    )
      return null;
  }
  const rounding = match[2];
  if (rounding === "w") date = addDays(date, (-(date.getUTCDay() + 6) % 7) + (end ? 6 : 0));
  if (rounding === "M") {
    date.setUTCDate(1);
    if (end) date.setUTCMonth(date.getUTCMonth() + 1, 0);
  }
  if (rounding === "y") date.setUTCMonth(end ? 11 : 0, end ? 31 : 1);
  return date;
}

/** From/to are inclusive UTC calendar dates, with optional Grafana-style date math. */
export function dateRangeBounds(value: DateRange, now = new Date()) {
  const today = parseDate(iso(now))!;
  const end = dateMath(value.to ?? "now", now, true);
  const start =
    value.from === undefined ? end && addDays(end, -29) : dateMath(value.from, now, false);
  if (!start || !end) return null;
  const days = (end.getTime() - start.getTime()) / DAY + 1;
  if (days < 1 || days > 3660 || end > today) return null;
  return { start: iso(start), end: iso(end) };
}

export function dateRangePresets() {
  return [
    { label: "Today", value: { from: "now/d", to: "now" } },
    { label: "Yesterday", value: { from: "now-1d/d", to: "now-1d/d" } },
    ...[3, 7, 14, 30].map((days) => ({
      label: `Last ${days} days`,
      value: { from: `now-${days - 1}d/d`, to: "now" },
    })),
    ...[3, 6, 9].map((months) => ({
      label: `Last ${months} months`,
      value: { from: `now-${months}M`, to: "now" },
    })),
    { label: "This year", value: { from: "now/y", to: "now" } },
    { label: "Last year", value: { from: "now-1y/y", to: "now-1y/y" } },
  ];
}

const sameRange = (left: DateRange, right: DateRange) =>
  left.from === right.from && left.to === right.to;

export function DateRangePicker({
  value,
  onChange,
  label = "Chart date range",
}: {
  value: DateRange;
  onChange: (value: DateRange) => void;
  label?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  const now = new Date();
  const today = iso(now);
  const presets = dateRangePresets();
  const selected = dateRangeBounds(value, now);
  const display =
    presets.find((preset) => sameRange(preset.value, value))?.label ??
    (selected ? `${selected.start} – ${selected.end}` : "Custom range");
  function choose(next: DateRange) {
    onChange(next);
    setOpen(false);
  }
  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        if (next) {
          const bounds = selected ?? dateRangeBounds(defaultDateRange, now)!;
          setStart(bounds.start);
          setEnd(bounds.end);
          setError("");
        }
        setOpen(next);
      }}
    >
      <Popover.Trigger asChild>
        <Button variant="outline" aria-label={label} aria-describedby={id} className="gap-2">
          <CalendarDays size={16} aria-hidden />
          <span id={id}>{display}</span>
          <ChevronDown size={14} aria-hidden />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Choose time range"
          style={{ width: "min(35rem, calc(100vw - 1.5rem))" }}
          className="z-50 max-h-[calc(100svh-2rem)] overflow-y-auto rounded-lg border bg-popover p-4 text-popover-foreground shadow-md outline-none"
        >
          <div className="grid gap-4 sm:grid-cols-[1fr_13rem]">
            <div className="sm:order-last sm:border-l sm:pl-4">
              <h2 className="mb-2 text-sm font-medium">Quick ranges</h2>
              <div className="grid grid-cols-2 gap-1 sm:grid-cols-1">
                {presets.map((preset) => (
                  <Button
                    key={`${preset.value.from}:${preset.value.to}`}
                    variant={sameRange(value, preset.value) ? "secondary" : "ghost"}
                    size="sm"
                    aria-pressed={sameRange(value, preset.value)}
                    className="justify-between gap-2"
                    onClick={() => choose(preset.value)}
                  >
                    {preset.label}
                    {sameRange(value, preset.value) && <Check size={14} aria-hidden />}
                  </Button>
                ))}
              </div>
            </div>
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                const next = { from: start, to: end };
                if (!dateRangeBounds(next, now)) {
                  setError("Choose valid dates spanning up to ten years.");
                  return;
                }
                choose(next);
              }}
            >
              <h2 className="text-sm font-medium">Custom range</h2>
              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">From</p>
                <DatePicker label="From" value={start} onChange={setStart} max={end || today} />
              </div>
              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">To</p>
                <DatePicker label="To" value={end} onChange={setEnd} min={start} max={today} />
              </div>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <div className="flex items-center justify-between gap-3">
                <Button type="submit" size="sm">
                  Apply time range
                </Button>
                <span className="text-xs text-muted-foreground">UTC</span>
              </div>
            </form>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
