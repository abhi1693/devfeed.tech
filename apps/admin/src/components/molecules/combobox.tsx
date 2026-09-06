"use client";

import { useId, useRef, useState, type AriaAttributes, type ReactNode } from "react";
import { CheckIcon, ChevronsUpDownIcon, LoaderCircleIcon } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/atoms/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { cn } from "@/lib/utils";

export type ComboboxOption = { value: string; label: string; description?: string; meta?: string; keywords?: string[]; group?: string };
export type ComboboxProps = AriaAttributes & {
  id?: string; name?: string; label: string; value: string; onChange: (value: string) => void;
  options: ComboboxOption[]; selectedOption?: ComboboxOption; required?: boolean; disabled?: boolean;
  placeholder?: string; clearLabel?: string; searchPlaceholder?: string; emptyMessage?: string; className?: string;
  search?: string; onSearchChange?: (value: string) => void; onOpenChange?: (open: boolean) => void;
  loading?: boolean; error?: string; onRetry?: () => void; footer?: ReactNode;
};

/** Searchable single-select. Domain data and remote pagination belong to callers. */
export function Combobox({ id, name, label, value, onChange, options, selectedOption, required = false, disabled = false,
  placeholder = "Select…", clearLabel = "None", searchPlaceholder, emptyMessage = "No results found.", className,
  search, onSearchChange, onOpenChange, loading = false, error, onRetry, footer, ...aria }: ComboboxProps) {
  const generatedId = useId();
  const triggerId = id ?? generatedId;
  const trigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [localSearch, setLocalSearch] = useState("");
  const [missing, setMissing] = useState(false);
  const query = search ?? localSearch;
  const selected = options.find(option => option.value === value) ?? (selectedOption?.value === value ? selectedOption : undefined);
  const choices = !required && !options.some(option => option.value === "") ? [{ value: "", label: clearLabel }, ...options] : options;
  const groups = new Map<string, ComboboxOption[]>();
  for (const option of choices) { const group = option.group ?? ""; groups.set(group, [...(groups.get(group) ?? []), option]); }
  const validationError = missing && required && !value;

  function changeSearch(next: string) { setLocalSearch(next); onSearchChange?.(next); }
  function changeOpen(next: boolean) {
    if (next && (disabled || trigger.current?.matches(":disabled"))) return;
    setOpen(next); changeSearch(""); onOpenChange?.(next);
  }
  function choose(next: string) {
    if (disabled || trigger.current?.matches(":disabled")) return;
    onChange(next); setMissing(false); changeOpen(false);
  }

  return <div className={cn("relative min-w-0", className)}>
    {/* Custom triggers are not constraint-validatable. This non-interactive bridge
        preserves native required validation and FormData, including fieldsets. */}
    {(name || required) && <select aria-hidden tabIndex={-1} name={name} required={required} disabled={disabled}
      className="pointer-events-none absolute size-px opacity-0" value={value} onChange={event => onChange(event.target.value)}
      onInvalid={event => {
        event.preventDefault(); setMissing(true);
        const first = event.currentTarget.form?.querySelector("input:invalid, select:invalid, textarea:invalid");
        if (!first || first === event.currentTarget) { trigger.current?.focus(); changeOpen(true); }
      }}>
      <option value="" />{value && <option value={value}>{selected?.label ?? value}</option>}
    </select>}
    <Popover open={open && !disabled} onOpenChange={changeOpen}>
      <PopoverTrigger asChild><Button ref={trigger} id={triggerId} type="button" variant="outline" role="combobox"
        aria-label={aria["aria-labelledby"] ? undefined : label} aria-expanded={open && !disabled} aria-required={required}
        {...aria} aria-invalid={validationError || aria["aria-invalid"]}
        aria-describedby={[aria["aria-describedby"], validationError ? `${triggerId}-required` : null].filter(Boolean).join(" ") || undefined}
        disabled={disabled} className="h-9 w-full justify-between gap-3 border-input bg-transparent px-3 text-base font-normal shadow-xs md:text-sm"
        onKeyDown={event => { if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); changeOpen(true); searchInput.current?.focus(); } }}>
        <span className={cn("min-w-0 truncate text-left", !value && "text-muted-foreground")}>{selected?.label ?? (value || placeholder)}</span>
        <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground/70" aria-hidden />
      </Button></PopoverTrigger>
      <PopoverContent aria-label={`${label} options`} className="flex max-h-(--radix-popover-content-available-height) w-(--radix-popover-trigger-width) min-w-56 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden">
        <Command loop shouldFilter={!onSearchChange} defaultValue={`option:${value}`} label={`${label} choices`} className="min-h-0">
          <CommandInput ref={searchInput} placeholder={searchPlaceholder ?? `Search ${label.toLowerCase()}…`} aria-label={`Search ${label.toLowerCase()}`}
            value={query} onValueChange={changeSearch} maxLength={200} />
          <CommandList aria-busy={loading}>
            {loading ? <div role="status" className="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground"><LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />Loading options…</div>
              : error ? <div role="alert" className="space-y-3 px-3 py-5 text-center text-sm"><p className="text-destructive">{error}</p>{onRetry && <Button type="button" size="sm" variant="outline" onClick={onRetry} onKeyDown={event => { if (["Enter", " "].includes(event.key)) event.stopPropagation(); }}>Retry</Button>}</div>
              : <>{onSearchChange && !options.length ? <p role="status" className="px-3 py-5 text-center text-sm text-muted-foreground">{emptyMessage}</p> : <CommandEmpty>{emptyMessage}</CommandEmpty>}{Array.from(groups, ([heading, items]) => <CommandGroup key={heading} heading={heading || undefined}>
                {items.map(option => <CommandItem key={option.value} value={`option:${option.value}`} keywords={[option.label, ...(option.keywords ?? [])]}
                  aria-label={[option.label, option.description, option.meta].filter(Boolean).join(" ")}
                  onSelect={() => choose(option.value)}>
                  <span className="min-w-0 flex-1"><span className="block break-words">{option.label}</span>{option.description && <span className="mt-0.5 block break-words text-xs text-muted-foreground">{option.description}</span>}</span>
                  {option.meta && <span className="max-w-28 truncate font-mono text-[11px] text-muted-foreground">{option.meta}</span>}
                  <CheckIcon className={cn("size-4 shrink-0", value !== option.value && "invisible")} aria-hidden />
                </CommandItem>)}
              </CommandGroup>)}</>}
          </CommandList>
        </Command>
        {!loading && !error && footer && <div className="shrink-0 border-t px-2 py-2">{footer}</div>}
      </PopoverContent>
    </Popover>
    {validationError && <p id={`${triggerId}-required`} role="alert" className="mt-2 text-xs text-destructive">Select {label.toLowerCase()}.</p>}
  </div>;
}
