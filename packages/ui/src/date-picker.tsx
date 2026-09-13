"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Popover } from "radix-ui";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Select } from "./select";

const iso = (date: Date) => date.toISOString().slice(0, 10);
function parse(value: string) {
  const date = new Date(`${value}T12:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(date.getTime()) && iso(date) === value
    ? date
    : null;
}
function today() {
  return iso(new Date());
}

const addDays = (date: Date, days: number) => {
  const next = new Date(date);
  next.setUTCDate(next.getUTCDate() + days);
  return next;
};
function addMonths(date: Date, months: number) {
  const next = new Date(date);
  next.setUTCDate(1);
  next.setUTCMonth(next.getUTCMonth() + months);
  const last = new Date(next);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  next.setUTCDate(Math.min(date.getUTCDate(), last.getUTCDate()));
  return next;
}
const monthOf = (value: string) => `${value.slice(0, 7)}-01`;
const format = (value: string, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en", { ...options, timeZone: "UTC" }).format(parse(value)!);

export function Calendar({
  value,
  onChange,
  min,
  max,
}: {
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
}) {
  const lower = min && parse(min) ? min : "0001-01-01";
  const upper = max && parse(max) ? max : "9999-12-31";
  const clamp = (date: string) => (date < lower ? lower : date > upper ? upper : date);
  const initial = clamp(parse(value) ? value : today());
  const [focused, setFocused] = useState(initial);
  const [month, setMonth] = useState(monthOf(initial));
  const grid = useRef<HTMLTableElement>(null);
  const moveFocus = useRef(false);
  const monthDate = parse(month)!;
  const year = monthDate.getUTCFullYear();
  const monthNumber = monthDate.getUTCMonth();
  const years = Array.from({ length: 201 }, (_, i) => 1900 + i);
  if (!years.includes(year)) years.push(year);
  const monthNames = Array.from({ length: 12 }, (_, i) =>
    new Intl.DateTimeFormat("en", { month: "long", timeZone: "UTC" }).format(
      new Date(Date.UTC(2020, i, 1)),
    ),
  );
  const first = addDays(monthDate, -monthDate.getUTCDay());
  const currentToday = today();
  useEffect(() => {
    if (moveFocus.current) {
      grid.current?.querySelector<HTMLButtonElement>(`[data-date="${focused}"]`)?.focus();
      moveFocus.current = false;
    }
  }, [focused, month]);
  function navigate(date: Date, keyboard = false) {
    const next =
      date.getUTCFullYear() < 1 ? lower : date.getUTCFullYear() > 9999 ? upper : clamp(iso(date));
    moveFocus.current = keyboard;
    setFocused(next);
    setMonth(monthOf(next));
  }
  function keydown(event: KeyboardEvent, date: Date) {
    let next: Date;
    switch (event.key) {
      case "ArrowLeft":
        next = addDays(date, -1);
        break;
      case "ArrowRight":
        next = addDays(date, 1);
        break;
      case "ArrowUp":
        next = addDays(date, -7);
        break;
      case "ArrowDown":
        next = addDays(date, 7);
        break;
      case "Home":
        next = addDays(date, -date.getUTCDay());
        break;
      case "End":
        next = addDays(date, 6 - date.getUTCDay());
        break;
      case "PageUp":
        next = addMonths(date, event.shiftKey ? -12 : -1);
        break;
      case "PageDown":
        next = addMonths(date, event.shiftKey ? 12 : 1);
        break;
      default:
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    navigate(next, true);
  }
  return (
    <div className="shared-calendar">
      <div className="shared-calendar-heading">
        <button
          type="button"
          aria-label="Previous month"
          disabled={month <= monthOf(lower)}
          onClick={() => navigate(addMonths(monthDate, -1))}
        >
          <ChevronLeft size={16} aria-hidden />
        </button>
        <Select
          label="Month"
          value={String(monthNumber)}
          required
          size="sm"
          onChange={(next) => navigate(addMonths(monthDate, Number(next) - monthNumber))}
          options={monthNames
            .map((label, i) => ({ value: String(i), label }))
            .filter((option) => {
              const candidate = `${String(year).padStart(4, "0")}-${String(Number(option.value) + 1).padStart(2, "0")}-01`;
              return candidate >= monthOf(lower) && candidate <= monthOf(upper);
            })}
        />
        <Select
          label="Year"
          value={String(year)}
          required
          size="sm"
          onChange={(next) => navigate(addMonths(monthDate, (Number(next) - year) * 12))}
          options={years
            .sort((a, b) => a - b)
            .filter((y) => y >= Number(lower.slice(0, 4)) && y <= Number(upper.slice(0, 4)))
            .map((y) => ({ value: String(y), label: String(y) }))}
        />
        <button
          type="button"
          aria-label="Next month"
          disabled={month >= monthOf(upper)}
          onClick={() => navigate(addMonths(monthDate, 1))}
        >
          <ChevronRight size={16} aria-hidden />
        </button>
      </div>
      <p className="shared-calendar-announcement" aria-live="polite">
        {format(month, { month: "long", year: "numeric" })}
      </p>
      <table ref={grid} role="grid" aria-label={format(month, { month: "long", year: "numeric" })}>
        <thead>
          <tr>
            {["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map(
              (day) => (
                <th key={day} scope="col" abbr={day}>
                  {day.slice(0, 2)}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: 6 }, (_, week) => (
            <tr key={week}>
              {Array.from({ length: 7 }, (_, day) => {
                const date = addDays(first, week * 7 + day);
                const key = iso(date);
                const outside = date.getUTCMonth() !== monthNumber;
                const disabled =
                  key < lower ||
                  key > upper ||
                  date.getUTCFullYear() < 1 ||
                  date.getUTCFullYear() > 9999;
                return (
                  <td key={day} aria-selected={key === value}>
                    <button
                      type="button"
                      data-date={key}
                      data-outside={outside}
                      data-selected={key === value}
                      aria-label={
                        date.getUTCFullYear() > 0 && date.getUTCFullYear() < 10000
                          ? format(key, { dateStyle: "full" })
                          : "Unavailable date"
                      }
                      aria-current={key === currentToday ? "date" : undefined}
                      tabIndex={key === focused ? 0 : -1}
                      disabled={disabled}
                      onFocus={() => setFocused(key)}
                      onKeyDown={(event) => keydown(event, date)}
                      onClick={() => onChange(key)}
                    >
                      {date.getUTCDate()}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DatePicker({
  id,
  name,
  label,
  value,
  onChange,
  min,
  max,
  disabled = false,
  placeholder = "Any date",
}: {
  id?: string;
  name?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const generated = useId();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  return (
    <div className="shared-date-picker">
      <input
        type="date"
        className="shared-date-native"
        aria-hidden
        tabIndex={-1}
        name={name}
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onInvalid={(event) => {
          event.preventDefault();
          trigger.current?.focus();
          setOpen(true);
        }}
      />
      <Popover.Root
        open={open && !disabled}
        onOpenChange={(next) => {
          if (next) setContainer(trigger.current?.closest("dialog") ?? document.body);
          setOpen(next);
        }}
      >
        <Popover.Trigger asChild>
          <button
            ref={trigger}
            id={id ?? generated}
            type="button"
            className="shared-select-trigger"
            aria-label={label}
            disabled={disabled}
          >
            <span>{parse(value) ? format(value, { dateStyle: "medium" }) : placeholder}</span>
            <CalendarDays size={16} aria-hidden />
          </button>
        </Popover.Trigger>
        <Popover.Portal container={container}>
          <Popover.Content
            ref={panel}
            className="shared-date-popover"
            align="start"
            sideOffset={6}
            collisionPadding={12}
            aria-label={label}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              panel.current
                ?.querySelector<HTMLButtonElement>('[role="grid"] button[tabindex="0"]')
                ?.focus();
            }}
          >
            <Calendar
              key={`${value}:${min}:${max}`}
              value={value}
              min={min}
              max={max}
              onChange={(next) => {
                onChange(next);
                setOpen(false);
              }}
            />
            <div className="shared-calendar-actions">
              <button
                type="button"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                Clear date
              </button>
              <button
                type="button"
                disabled={(!!min && today() < min) || (!!max && today() > max)}
                onClick={() => {
                  onChange(today());
                  setOpen(false);
                }}
              >
                Today
              </button>
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
