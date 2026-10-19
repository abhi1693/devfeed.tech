"use client";

import type { ComponentProps, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ChevronsLeft, ChevronsRight, Columns3, MoreHorizontal } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Input } from "@devfeed/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@devfeed/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { Select } from "@/components/molecules/select";
import { useBoardMotion } from "@/lib/use-board-motion";
import type { useBoardColumns } from "@/lib/use-board-columns";
import { cn } from "@/lib/utils";

type Labels<Stage extends string> = Record<Stage, string>;

export function BoardColumnsMenu<Stage extends string>({
  stages,
  labels,
  columns,
  label,
}: {
  stages: readonly Stage[];
  labels: Labels<Stage>;
  columns: ReturnType<typeof useBoardColumns<Stage>>;
  label: string;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="sm:ml-auto">
          <Columns3 aria-hidden="true" />
          Columns
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" aria-label={label} className="w-56 p-2">
        <p className="px-2 py-1.5 text-sm font-medium">Show columns</p>
        {stages.map((stage) => {
          const checked = columns.visible.includes(stage);
          return (
            <label
              key={stage}
              className="flex cursor-pointer items-center gap-3 rounded px-2 py-2 text-sm hover:bg-muted has-disabled:cursor-default has-disabled:text-muted-foreground"
            >
              <Input
                type="checkbox"
                checked={checked}
                disabled={checked && columns.visible.length === 1}
                onChange={(event) => columns.setVisible(stage, event.target.checked)}
              />
              {labels[stage]}
            </label>
          );
        })}
        <div className="mt-1 border-t pt-1">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={columns.showAll}
          >
            Show all columns
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Presentation only: callers provide stages, counts, cards and permitted actions. */
export function KanbanBoard<Stage extends string>({
  label,
  stageLabel,
  stages,
  labels,
  counts,
  selected,
  onSelect,
  layoutKey,
  children,
}: {
  label: string;
  stageLabel: string;
  stages: readonly Stage[];
  labels: Labels<Stage>;
  counts: Partial<Record<Stage, number>>;
  selected: Stage;
  onSelect: (stage: Stage) => void;
  layoutKey: string;
  children: ReactNode;
}) {
  const motion = useBoardMotion(layoutKey);
  return (
    <>
      <div className="lg:hidden">
        <Select
          label={stageLabel}
          value={selected}
          onChange={(value) => {
            const stage = stages.find((stage) => stage === value);
            if (stage) onSelect(stage);
          }}
          required
          options={stages.map((stage) => ({
            value: stage,
            label: `${labels[stage]} (${counts[stage] ?? 0})`,
          }))}
        />
      </div>
      <div
        ref={motion}
        role="region"
        aria-label={label}
        className="max-w-full overflow-x-auto rounded-lg"
      >
        <div className="grid gap-4 lg:flex lg:w-max lg:items-stretch">{children}</div>
      </div>
    </>
  );
}

export function KanbanColumn({
  id,
  contentId,
  label,
  total,
  countLabel,
  collapsed,
  selected,
  onCollapse,
  dropAllowed,
  dropActive,
  children,
  className,
  ...props
}: Omit<ComponentProps<"section">, "id" | "onCollapse"> & {
  id: string;
  contentId: string;
  label: string;
  total: number;
  countLabel: string;
  collapsed: boolean;
  selected: boolean;
  onCollapse: () => void;
  dropAllowed?: boolean;
  dropActive?: boolean;
}) {
  return (
    <section
      {...props}
      aria-labelledby={id}
      data-pipeline-collapsed={collapsed}
      data-pipeline-drop-target={dropAllowed || undefined}
      className={cn(
        "min-w-0 rounded-lg border bg-muted/30 lg:shrink-0",
        collapsed ? "lg:w-12" : "lg:w-72",
        !selected && "hidden lg:block",
        dropAllowed && (dropActive ? "ring-2 ring-primary" : "ring-1 ring-primary/40"),
        className,
      )}
    >
      <div
        className={`flex select-none items-center gap-2 border-b px-4 py-3 ${collapsed ? "lg:h-full lg:min-h-40 lg:flex-col lg:border-b-0 lg:px-2" : ""}`}
        onDoubleClick={(event) => {
          if (window.innerWidth >= 1024 && !(event.target as HTMLElement).closest("button"))
            onCollapse();
        }}
      >
        <h2
          id={id}
          className={`min-w-0 flex-1 text-sm font-semibold ${collapsed ? "lg:order-2 lg:flex-none lg:[writing-mode:vertical-rl]" : ""}`}
        >
          {label}
        </h2>
        <span
          aria-label={countLabel}
          className={`rounded-full bg-background px-2 py-0.5 text-xs tabular-nums ${collapsed ? "lg:order-3" : ""}`}
        >
          {total.toLocaleString()}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`hidden size-6 shrink-0 lg:inline-flex ${collapsed ? "lg:order-1" : ""}`}
          aria-label={`${collapsed ? "Expand" : "Collapse"} ${label} column`}
          aria-expanded={!collapsed}
          aria-controls={contentId}
          title={`${collapsed ? "Expand" : "Collapse"} column`}
          onClick={onCollapse}
        >
          {collapsed ? (
            <ChevronsRight aria-hidden="true" className="size-3.5" />
          ) : (
            <ChevronsLeft aria-hidden="true" className="size-3.5" />
          )}
        </Button>
      </div>
      <div id={contentId} className={`space-y-3 p-3 ${collapsed ? "lg:hidden" : ""}`}>
        {children}
      </div>
    </section>
  );
}

export function KanbanCard({
  status,
  title,
  actions,
  children,
  className = "",
  ...props
}: Omit<ComponentProps<"article">, "title"> & {
  status: { label: string; icon: LucideIcon; color: string; card: string };
  title: ReactNode;
  actions?: ReactNode;
}) {
  const Icon = status.icon;
  return (
    <article
      {...props}
      className={cn("relative rounded-md border border-l-4 p-3 shadow-sm", status.card, className)}
    >
      <div className="flex items-start gap-2">
        <h3 className="min-w-0 flex-1 break-words text-sm font-medium leading-snug">{title}</h3>
        <span
          role="img"
          aria-label={status.label}
          title={status.label}
          className={`mt-0.5 shrink-0 ${status.color}`}
        >
          <Icon aria-hidden="true" className="size-3.5" />
        </span>
        {actions}
      </div>
      {children}
    </article>
  );
}

export function BoardCardActions({
  label,
  disabled,
  children,
}: {
  label: string;
  disabled: boolean;
  children: ReactNode;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="-my-1 -mr-1 size-6 shrink-0"
          aria-label={label}
          disabled={disabled}
        >
          <MoreHorizontal aria-hidden="true" className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent aria-label={label} align="end">
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function BoardLoadMore({
  available,
  loading,
  disabled,
  error,
  onLoad,
  children,
}: {
  available: boolean;
  loading: boolean;
  disabled: boolean;
  error?: string;
  onLoad: () => void;
  children: ReactNode;
}) {
  return (
    <>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {available && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          disabled={disabled}
          loading={loading}
          onClick={onLoad}
        >
          {children}
        </Button>
      )}
    </>
  );
}
