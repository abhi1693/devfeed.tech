"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type AriaAttributes,
  type ReactNode,
  type ComponentProps,
} from "react";
import { CheckIcon, ChevronDownIcon, LoaderCircleIcon } from "lucide-react";
import { Popover as PopoverPrimitive } from "radix-ui";
import { Command, useCommandState } from "cmdk";
const Popover = PopoverPrimitive.Root;
const PopoverTrigger = PopoverPrimitive.Trigger;
const CommandInput = Command.Input;
const CommandList = Command.List;
const CommandEmpty = Command.Empty;
const CommandGroup = Command.Group;
const CommandItem = Command.Item;
const cn = (...values: (string | false | undefined)[]) => values.filter(Boolean).join(" ");
function PopoverContent({
  children,
  className,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        {...props}
        collisionPadding={12}
        sideOffset={6}
        className={cn("shared-select-popover", className)}
      >
        {children}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}

export type SelectOption = {
  value: string;
  label: string;
  description?: string;
  meta?: string;
  keywords?: string[];
  group?: string;
};
export type SelectProps = AriaAttributes & {
  id?: string;
  name?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  selectedOption?: SelectOption;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  clearLabel?: string;
  emptyMessage?: string;
  className?: string;
  size?: "default" | "sm";
  icon?: ReactNode;
  align?: "start" | "end";
  search?: { value?: string; onChange?: (value: string) => void; placeholder?: string };
  onOpenChange?: (open: boolean) => void;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  footer?: ReactNode;
};

/** Shared single-select; Combobox adds search through the optional search configuration. */
export function Select({
  id,
  name,
  label,
  value,
  onChange,
  options,
  selectedOption,
  required = false,
  disabled = false,
  placeholder = "Select…",
  clearLabel = "None",
  emptyMessage = "No results found.",
  className,
  size = "default",
  icon,
  align = "start",
  search,
  onOpenChange,
  loading = false,
  error,
  onRetry,
  footer,
  ...aria
}: SelectProps) {
  const generatedId = useId();
  const triggerId = id ?? generatedId;
  const trigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [localSearch, setLocalSearch] = useState("");
  const [missing, setMissing] = useState(false);
  const query = search?.value ?? localSearch;
  const [highlighted, setHighlighted] = useState(`option:${value}`);
  const typeahead = useRef({ text: "", time: 0 });
  const selected =
    options.find((option) => option.value === value) ??
    (selectedOption?.value === value ? selectedOption : undefined);
  const choices =
    !required && !options.some((option) => option.value === "")
      ? [{ value: "", label: clearLabel }, ...options]
      : options;
  const groups = new Map<string, SelectOption[]>();
  for (const option of choices) {
    const group = option.group ?? "";
    groups.set(group, [...(groups.get(group) ?? []), option]);
  }
  const validationError = missing && required && !value;

  function changeSearch(next: string) {
    setLocalSearch(next);
    search?.onChange?.(next);
  }
  function changeOpen(next: boolean) {
    if (next && (disabled || trigger.current?.matches(":disabled"))) return;
    setOpen(next);
    changeSearch("");
    if (next) {
      setHighlighted(`option:${value}`);
      typeahead.current = { text: "", time: 0 };
    }
    onOpenChange?.(next);
  }
  function choose(next: string) {
    if (disabled || trigger.current?.matches(":disabled")) return;
    onChange(next);
    setMissing(false);
    changeOpen(false);
  }

  return (
    <div className={cn("shared-select relative min-w-0", className)}>
      {/* Custom triggers are not constraint-validatable. This non-interactive bridge
        preserves native required validation and FormData, including fieldsets. */}
      {(name || required) && (
        <select
          aria-hidden
          tabIndex={-1}
          name={name}
          required={required}
          disabled={disabled}
          className="shared-select-native pointer-events-none absolute size-px opacity-0"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onInvalid={(event) => {
            event.preventDefault();
            setMissing(true);
            const first = event.currentTarget.form?.querySelector(
              "input:invalid, select:invalid, textarea:invalid",
            );
            if (!first || first === event.currentTarget) {
              trigger.current?.focus();
              changeOpen(true);
            }
          }}
        >
          <option value="" />
          {value && <option value={value}>{selected?.label ?? value}</option>}
        </select>
      )}
      <Popover open={open && !disabled} onOpenChange={changeOpen}>
        <PopoverTrigger asChild>
          <button
            ref={trigger}
            id={triggerId}
            type="button"
            role="combobox"
            aria-label={aria["aria-labelledby"] ? undefined : label}
            aria-expanded={open && !disabled}
            aria-required={required}
            {...aria}
            aria-invalid={validationError || aria["aria-invalid"]}
            aria-describedby={
              [aria["aria-describedby"], validationError ? `${triggerId}-required` : null]
                .filter(Boolean)
                .join(" ") || undefined
            }
            disabled={disabled}
            data-size={size}
            className="shared-select-trigger cursor-pointer w-full justify-between gap-2 border-input px-3 text-base font-normal shadow-xs md:text-sm"
            onKeyDown={(event) => {
              if (["ArrowDown", "ArrowUp"].includes(event.key)) {
                event.preventDefault();
                changeOpen(true);
                searchInput.current?.focus();
              }
            }}
          >
            {icon}
            <span
              className={cn(
                "min-w-0 flex-1 truncate text-left",
                !value && !selected && "text-muted-foreground",
              )}
            >
              {selected?.label ?? (value || placeholder)}
            </span>
            <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground/70" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align={align}
          aria-label={`${label} options`}
          className={cn(
            "flex max-h-(--radix-popover-content-available-height) w-(--radix-popover-trigger-width) max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden",
            search ? "min-w-56" : "min-w-32",
          )}
        >
          <Command
            loop
            shouldFilter={!!search && !search.onChange}
            value={highlighted}
            onValueChange={setHighlighted}
            label={`${label} choices`}
            className="min-h-0"
            onKeyDown={(event) => {
              if (
                search ||
                event.target instanceof HTMLInputElement ||
                event.target instanceof HTMLButtonElement
              )
                return;
              if (event.key === " ") {
                event.preventDefault();
                const option = choices.find((option) => `option:${option.value}` === highlighted);
                if (option) choose(option.value);
                return;
              }
              if (event.key.length !== 1 || event.ctrlKey || event.altKey || event.metaKey) return;
              event.preventDefault();
              const now = event.timeStamp;
              const text =
                now - typeahead.current.time < 700
                  ? typeahead.current.text + event.key.toLowerCase()
                  : event.key.toLowerCase();
              typeahead.current = { text, time: now };
              const match = choices.find((option) => option.label.toLowerCase().startsWith(text));
              if (match) setHighlighted(`option:${match.value}`);
            }}
          >
            {search && (
              <CommandInput
                ref={searchInput}
                placeholder={search.placeholder ?? `Search ${label.toLowerCase()}…`}
                aria-label={`Search ${label.toLowerCase()}`}
                value={query}
                onValueChange={changeSearch}
                maxLength={200}
              />
            )}
            <CommandList
              aria-label={`${label} choices`}
              aria-busy={loading}
              tabIndex={search ? undefined : -1}
            >
              {loading ? (
                <div
                  role="status"
                  className="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground"
                >
                  <LoaderCircleIcon
                    className="size-4 animate-spin motion-reduce:animate-none"
                    aria-hidden
                  />
                  Loading options…
                </div>
              ) : error ? (
                <div role="alert" className="space-y-3 px-3 py-5 text-center text-sm">
                  <p className="text-destructive">{error}</p>
                  {onRetry && (
                    <button
                      type="button"
                      onClick={onRetry}
                      onKeyDown={(event) => {
                        if (["Enter", " "].includes(event.key)) event.stopPropagation();
                      }}
                    >
                      Retry
                    </button>
                  )}
                </div>
              ) : (
                <>
                  {search?.onChange && !options.length ? (
                    <p
                      role="status"
                      className="px-3 py-5 text-center text-sm text-muted-foreground"
                    >
                      {emptyMessage}
                    </p>
                  ) : (
                    <CommandEmpty>{emptyMessage}</CommandEmpty>
                  )}
                  {Array.from(groups, ([heading, items]) => (
                    <CommandGroup key={heading} heading={heading || undefined}>
                      {items.map((option) => (
                        <SelectItem
                          key={option.value}
                          option={option}
                          selected={value === option.value}
                          searchable={!!search}
                          onSelect={() => choose(option.value)}
                        />
                      ))}
                    </CommandGroup>
                  ))}
                </>
              )}
            </CommandList>
          </Command>
          {!loading && !error && footer && (
            <div className="shrink-0 border-t px-2 py-2">{footer}</div>
          )}
        </PopoverContent>
      </Popover>
      {validationError && (
        <p id={`${triggerId}-required`} role="alert" className="mt-2 text-xs text-destructive">
          Select {label.toLowerCase()}.
        </p>
      )}
    </div>
  );
}

/** Shared option layout and keyboard focus for both dropdown variants. */
function SelectItem({
  option,
  selected,
  searchable,
  onSelect,
}: {
  option: SelectOption;
  selected: boolean;
  searchable: boolean;
  onSelect: () => void;
}) {
  const item = useRef<HTMLDivElement>(null);
  const active = useCommandState((state) => state.value === `option:${option.value}`);
  useEffect(() => {
    if (active && !searchable) item.current?.focus({ preventScroll: true });
  }, [active, searchable]);
  return (
    <CommandItem
      ref={item}
      tabIndex={searchable ? undefined : -1}
      value={`option:${option.value}`}
      keywords={[option.label, ...(option.keywords ?? [])]}
      aria-label={[option.label, option.description, option.meta].filter(Boolean).join(" ")}
      onSelect={onSelect}
    >
      <span className="min-w-0 flex-1">
        <span className="block break-words">{option.label}</span>
        {option.description && (
          <span className="mt-0.5 block break-words text-xs text-muted-foreground">
            {option.description}
          </span>
        )}
      </span>
      {option.meta && (
        <span className="max-w-28 truncate font-mono text-[11px] text-muted-foreground">
          {option.meta}
        </span>
      )}
      <CheckIcon
        data-checked={selected}
        className={cn("size-4 shrink-0", !selected && "invisible")}
        aria-hidden
      />
    </CommandItem>
  );
}
