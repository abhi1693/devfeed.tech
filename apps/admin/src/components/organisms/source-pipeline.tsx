"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { ChevronsLeft, ChevronsRight, Columns3, MoreHorizontal, X } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { Input } from "@devfeed/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@devfeed/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { useRequest } from "@/lib/use-request";
import { useSourcePipelineInterval } from "@/lib/use-source-pipeline-interval";
import { useBoardColumns } from "@/lib/use-board-columns";
import { useBoardMotion } from "@/lib/use-board-motion";
import { sourceCardStatuses } from "@/lib/source-pipeline-status";
import { notify, notifyFailure } from "@/lib/notifications";
import {
  advanceSourcePipeline,
  getSourcePipeline,
  getSourcePipelineColumn,
  retrySourcePipeline,
  sourceColumnsStorageKey,
  sourcePipelineHost,
  sourcePipelineLabels as labels,
  sourcePipelineStages as stages,
  sourceRetryLabel,
  type SourcePipelineBoard,
  type SourcePipelineColumn,
  type SourcePipelineCard,
  type SourcePipelineStage,
} from "@/lib/source-pipeline";
import { SourceReadiness } from "./source-readiness";

type RetryTarget = SourcePipelineCard["retry_targets"][number];
const dragType = "application/x-devfeed-source-pipeline";

export function SourcePipeline({ query }: { query: string }) {
  const router = useRouter();
  const admin = useAdmin();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const pending = useRef(false);
  const [candidate, setCandidate] = useState<SourcePipelineCard | null>(null);
  const columns = useBoardColumns(sourceColumnsStorageKey, stages);
  const seconds = useSourcePipelineInterval();
  const load = useCallback((signal: AbortSignal) => getSourcePipeline(query, signal), [query]);
  const result = useRequest(`source-pipeline/${query}`, load, seconds * 1000, revision);
  const selected =
    columns.visible.find((stage) => stage === search.get("stage")) ??
    result.data?.columns.find(
      (column) => column.total > 0 && columns.visible.includes(column.stage),
    )?.stage ??
    columns.visible[0];
  function change(values: Record<string, string>, replace = false) {
    const next = new URLSearchParams(query);
    next.set("view", "pipeline");
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    router[replace ? "replace" : "push"](`/content/sources?${next}`, { scroll: false });
  }
  async function run(item: SourcePipelineCard, target?: RetryTarget) {
    if (pending.current || (!target && !item.can_advance)) return;
    if (
      target &&
      !item.retry_targets.some(
        (allowed) => allowed.stage === target.stage && allowed.job_id === target.job_id,
      )
    )
      return;
    pending.current = true;
    setBusy(item.key);
    try {
      if (target) await retrySourcePipeline(item, target, admin.csrf_token);
      else await advanceSourcePipeline(item, admin.csrf_token);
      notify.success(target ? `${labels[target.stage]} retry queued` : "Source processing updated");
    } catch (cause) {
      notifyFailure(cause, "Could not update source processing");
    } finally {
      pending.current = false;
      setBusy(null);
      setRevision((value) => value + 1);
    }
  }
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <SearchField
          className="min-w-48 flex-1 sm:max-w-md"
          label="Search source pipeline"
          value={search.get("q") ?? ""}
          scopeKey={searchScope(query)}
          onSearch={(q) => change({ q }, true)}
        />
        <Select
          label="Source type"
          className="w-full sm:w-48"
          value={search.get("source_type") ?? ""}
          onChange={(source_type) => change({ source_type })}
          placeholder="All source types"
          clearLabel="All source types"
          options={[
            { value: "publisher", label: "Publisher" },
            { value: "aggregator", label: "Aggregator" },
          ]}
        />
        <Select
          label="Pipeline status"
          className="w-full sm:w-48"
          value={search.get("needs_attention") === "true" ? "true" : ""}
          onChange={(needs_attention) => change({ needs_attention })}
          placeholder="All sources"
          clearLabel="All sources"
          options={[{ value: "true", label: "Needs attention" }]}
        />
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="sm:ml-auto">
              <Columns3 aria-hidden="true" />
              Columns
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" aria-label="Source pipeline columns" className="w-56 p-2">
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
      </div>
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      {result.data && (
        <SourceColumns
          key={query}
          data={result.data}
          query={query}
          stage={selected}
          onStage={(stage) => change({ stage })}
          visible={columns.visible}
          collapsed={columns.collapsed}
          onCollapse={columns.toggleCollapsed}
          busy={busy}
          onRun={(item, target) => void run(item, target)}
          onCandidate={setCandidate}
        />
      )}
      <Dialog.Root
        open={candidate !== null}
        onOpenChange={(open) => {
          if (!open) setCandidate(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%-2rem)] max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-5 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <Dialog.Title className="text-lg font-semibold">
                  {candidate && (candidate.name || sourcePipelineHost(candidate))}
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                  Review this source candidate’s preparation and processing.
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button variant="ghost" size="icon" aria-label="Close candidate details">
                  <X aria-hidden="true" />
                </Button>
              </Dialog.Close>
            </div>
            {candidate && (
              <SourceReadiness
                key={candidate.key}
                kind={candidate.kind}
                id={candidate.id}
                onUpdated={() => setRevision((value) => value + 1)}
              />
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function compareItems(a: SourcePipelineCard, b: SourcePipelineCard) {
  const running = Number(b.state === "running") - Number(a.state === "running");
  if (running) return running;
  const created = Date.parse(a.created_at) - Date.parse(b.created_at);
  if (created) return created;
  const fractionA = (a.created_at.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
  const fractionB = (b.created_at.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
  return fractionA.localeCompare(fractionB) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
}

function SourceColumns({
  data,
  query,
  stage,
  onStage,
  visible,
  collapsed,
  onCollapse,
  busy,
  onRun,
  onCandidate,
}: {
  data: SourcePipelineBoard;
  query: string;
  stage: SourcePipelineStage;
  onStage: (stage: SourcePipelineStage) => void;
  visible: SourcePipelineStage[];
  collapsed: SourcePipelineStage[];
  onCollapse: (stage: SourcePipelineStage) => void;
  busy: string | null;
  onRun: (item: SourcePipelineCard, target?: RetryTarget) => void;
  onCandidate: (item: SourcePipelineCard) => void;
}) {
  const motion = useBoardMotion(`${visible.join(",")}|${collapsed.join(",")}`);
  const [additional, setAdditional] = useState<{
    snapshot: SourcePipelineBoard;
    columns: Partial<Record<SourcePipelineStage, SourcePipelineColumn>>;
  }>();
  const [loadingMore, setLoadingMore] = useState<SourcePipelineStage>();
  const [error, setError] = useState<{
    snapshot: SourcePipelineBoard;
    stage: SourcePipelineStage;
  }>();
  const current = useRef(data);
  const request = useRef<AbortController | null>(null);
  const dragging = useRef<SourcePipelineCard | null>(null);
  const [dragged, setDragged] = useState<SourcePipelineCard | null>(null);
  const [dropStage, setDropStage] = useState<SourcePipelineStage | null>(null);
  useEffect(() => {
    current.current = data;
    return () => request.current?.abort();
  }, [data]);
  function clearDrag() {
    dragging.current = null;
    setDragged(null);
    setDropStage(null);
  }
  const extra = additional?.snapshot === data ? additional.columns : {};
  const items = new Map<string, SourcePipelineCard>();
  data.columns.forEach((column) => column.items.forEach((item) => items.set(item.key, item)));
  Object.values(extra).forEach((column) =>
    column?.items.forEach((item) => items.set(item.key, item)),
  );
  async function more(column: SourcePipelineColumn) {
    if (column.next_cursor == null || loadingMore) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoadingMore(column.stage);
    setError(undefined);
    try {
      const next = await getSourcePipelineColumn(
        column.stage,
        query,
        column.next_cursor,
        controller.signal,
      );
      if (!controller.signal.aborted && current.current === data)
        setAdditional((previous) => ({
          snapshot: data,
          columns: {
            ...(previous?.snapshot === data ? previous.columns : {}),
            [column.stage]: { ...next, items: [...column.items, ...next.items] },
          },
        }));
    } catch (cause) {
      if (!controller.signal.aborted) {
        notifyFailure(cause, "Could not load more sources");
        setError({ snapshot: data, stage: column.stage });
      }
    } finally {
      if (request.current === controller) setLoadingMore(undefined);
    }
  }
  return (
    <>
      <div className="lg:hidden">
        <Select
          label="Source pipeline stage"
          value={stage}
          onChange={(value) => onStage(value as SourcePipelineStage)}
          required
          options={visible.map((value) => ({
            value,
            label: `${labels[value]} (${data.counts[value] ?? 0})`,
          }))}
        />
      </div>
      <div
        ref={motion}
        role="region"
        aria-label="Source lifecycle pipeline"
        tabIndex={0}
        className="max-w-full overflow-x-auto rounded-lg focus-visible:outline-2 focus-visible:outline-ring"
      >
        <div className="grid gap-4 lg:flex lg:w-max lg:items-stretch">
          {visible.map((value) => {
            const folded = collapsed.includes(value);
            const column = extra[value] ??
              data.columns.find((column) => column.stage === value) ?? {
                stage: value,
                total: data.counts[value] ?? 0,
                items: [],
                next_cursor: null,
              };
            const cards = [...items.values()]
              .filter((item) => item.stage === value)
              .sort(["active", "paused", "rejected"].includes(value) ? () => 0 : compareItems);
            const canDrop =
              !busy && !!dragged?.retry_targets.some((target) => target.stage === value);
            return (
              <section
                key={value}
                aria-labelledby={`source-pipeline-${value}`}
                data-pipeline-collapsed={folded}
                data-pipeline-drop-target={canDrop || undefined}
                className={`min-w-0 rounded-lg border bg-muted/30 lg:shrink-0 ${folded ? "lg:w-12" : "lg:w-72"} ${stage === value ? "" : "hidden lg:block"} ${canDrop ? (dropStage === value ? "ring-2 ring-primary" : "ring-1 ring-primary/40") : ""}`}
                onDragOver={(event) => {
                  if (!canDrop || !dragging.current) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setDropStage(value);
                }}
                onDragLeave={(event) => {
                  if (
                    !(event.relatedTarget instanceof Node) ||
                    !event.currentTarget.contains(event.relatedTarget)
                  )
                    setDropStage(null);
                }}
                onDrop={(event) => {
                  const item = dragging.current;
                  const target = item?.retry_targets.find((target) => target.stage === value);
                  if (!item || !target || busy || event.dataTransfer.getData(dragType) !== item.key)
                    return;
                  event.preventDefault();
                  clearDrag();
                  onRun(item, target);
                }}
              >
                <div
                  className={`flex select-none items-center gap-2 border-b px-4 py-3 ${folded ? "lg:h-full lg:min-h-40 lg:flex-col lg:border-b-0 lg:px-2" : ""}`}
                  onDoubleClick={(event) => {
                    if (
                      window.innerWidth >= 1024 &&
                      !(event.target as HTMLElement).closest("button")
                    )
                      onCollapse(value);
                  }}
                >
                  <h2
                    id={`source-pipeline-${value}`}
                    className={`min-w-0 flex-1 text-sm font-semibold ${folded ? "lg:order-2 lg:flex-none lg:[writing-mode:vertical-rl]" : ""}`}
                  >
                    {labels[value]}
                  </h2>
                  <span
                    aria-label={`${column.total} sources`}
                    className={`rounded-full bg-background px-2 py-0.5 text-xs tabular-nums ${folded ? "lg:order-3" : ""}`}
                  >
                    {column.total.toLocaleString()}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className={`hidden size-6 shrink-0 lg:inline-flex ${folded ? "lg:order-1" : ""}`}
                    aria-label={`${folded ? "Expand" : "Collapse"} ${labels[value]} column`}
                    aria-expanded={!folded}
                    aria-controls={`source-pipeline-${value}-items`}
                    onClick={() => onCollapse(value)}
                  >
                    {folded ? (
                      <ChevronsRight aria-hidden="true" className="size-3.5" />
                    ) : (
                      <ChevronsLeft aria-hidden="true" className="size-3.5" />
                    )}
                  </Button>
                </div>
                <div
                  id={`source-pipeline-${value}-items`}
                  className={`space-y-3 p-3 ${folded ? "lg:hidden" : ""}`}
                >
                  {!cards.length && (
                    <p className="py-3 text-center text-xs text-muted-foreground">
                      No sources in this stage.
                    </p>
                  )}
                  {cards.map((item) => (
                    <SourceCard
                      key={item.key}
                      item={item}
                      busy={busy !== null}
                      onRun={onRun}
                      onCandidate={onCandidate}
                      onDragEnd={clearDrag}
                      onDragStart={(event) => {
                        if (
                          busy ||
                          !item.retry_targets.length ||
                          (event.target as HTMLElement).closest("button, a")
                        ) {
                          event.preventDefault();
                          return;
                        }
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData(dragType, item.key);
                        event.dataTransfer.setData("text/plain", item.key);
                        dragging.current = item;
                        setDragged(item);
                      }}
                    />
                  ))}
                  {error?.snapshot === data && error.stage === value && (
                    <p role="alert" className="text-xs text-destructive">
                      Could not load more sources. Try again.
                    </p>
                  )}
                  {column.next_cursor != null && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      disabled={!!loadingMore}
                      loading={loadingMore === value}
                      onClick={() => void more(column)}
                    >
                      Load more {labels[value].toLowerCase()} sources
                    </Button>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </>
  );
}

function SourceCard({
  item,
  busy,
  onRun,
  onCandidate,
  onDragStart,
  onDragEnd,
}: {
  item: SourcePipelineCard;
  busy: boolean;
  onRun: (item: SourcePipelineCard, target?: RetryTarget) => void;
  onCandidate: (item: SourcePipelineCard) => void;
  onDragStart: React.DragEventHandler<HTMLElement>;
  onDragEnd: React.DragEventHandler<HTMLElement>;
}) {
  const status =
    sourceCardStatuses[
      item.stage === "active" || item.stage === "paused" || item.stage === "rejected"
        ? item.stage
        : item.state
    ] ?? sourceCardStatuses.blocked;
  const Icon = status.icon;
  const name = item.name || sourcePipelineHost(item);
  const titleClass =
    "rounded-sm text-sm font-medium leading-snug hover:underline focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <article
      data-board-item-id={item.key}
      data-board-stage={item.stage}
      data-source-pipeline-kind={item.kind}
      data-source-pipeline-id={item.id}
      data-pipeline-state={item.state}
      draggable={!busy && !!item.retry_targets.length}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={`relative rounded-md border border-l-4 p-3 shadow-sm ${status.card}`}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 flex-1 break-words">
          {item.kind === "source" ? (
            <Link href={`/content/sources/${item.id}`} draggable={false} className={titleClass}>
              {name}
            </Link>
          ) : (
            <button
              type="button"
              className={`${titleClass} text-left`}
              onClick={() => onCandidate(item)}
            >
              {name}
            </button>
          )}
        </h3>
        <span
          role="img"
          aria-label={status.label}
          title={status.label}
          className={`mt-0.5 shrink-0 ${status.color}`}
        >
          <Icon aria-hidden="true" className="size-3.5" />
        </span>
        {(item.can_advance || item.can_approve || !!item.retry_targets.length) && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="-my-1 -mr-1 size-6 shrink-0"
                aria-label={`Source actions ${name}`}
                disabled={busy}
              >
                <MoreHorizontal aria-hidden="true" className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {item.kind === "source" && item.can_approve && (
                <DropdownMenuItem asChild>
                  <Link href={`/content/sources/${item.id}/review`}>Review source</Link>
                </DropdownMenuItem>
              )}
              {item.can_advance && (
                <DropdownMenuItem onSelect={() => onRun(item)}>
                  {item.advance_label || "Run next step"}
                </DropdownMenuItem>
              )}
              {item.retry_targets.map((target) => (
                <DropdownMenuItem key={target.stage} onSelect={() => onRun(item, target)}>
                  {sourceRetryLabel(item, target.stage)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      <p className="mt-1 truncate text-xs text-muted-foreground">{sourcePipelineHost(item)}</p>
    </article>
  );
}
