"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { X } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { DropdownMenuItem } from "@devfeed/ui/dropdown-menu";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { SnapshotStatus } from "@/components/molecules/snapshot-status";
import { useAdmin } from "@/components/molecules/admin-session";
import { useRequest } from "@/lib/use-request";
import { useBoardRefreshInterval } from "@/lib/use-refresh-interval";
import { useBoardColumns } from "@/lib/use-board-columns";
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

import {
  BoardColumnsMenu,
  KanbanBoard,
  KanbanColumn,
  KanbanCard,
  BoardCardActions,
  BoardLoadMore,
} from "./kanban-board";
import { useBoardDrag } from "@/lib/use-board-drag";
import { useBoardPagination } from "@/lib/use-board-pagination";
import { boardItems, compareBoardItems } from "@/lib/board-items";

export function SourcePipeline({ query }: { query: string }) {
  const router = useRouter();
  const admin = useAdmin();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const pending = useRef(false);
  const [candidate, setCandidate] = useState<SourcePipelineCard | null>(null);
  const columns = useBoardColumns(sourceColumnsStorageKey, stages);
  const seconds = useBoardRefreshInterval();
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
        <BoardColumnsMenu
          stages={stages}
          labels={labels}
          columns={columns}
          label="Source pipeline columns"
        />
      </div>
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      {!result.error && (
        <SnapshotStatus snapshot={result.data?.snapshot} checkedAt={result.data?.as_of} />
      )}
      {!result.error && result.data && (
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
  const pagination = useBoardPagination<
    SourcePipelineBoard,
    SourcePipelineStage,
    SourcePipelineColumn
  >({
    snapshot: data,
    load: (column, signal) =>
      getSourcePipelineColumn(column.stage, query, column.next_cursor!, signal),
    merge: (column, next) => ({ ...next, items: [...column.items, ...next.items] }),
    failureMessage: "Could not load more sources",
  });
  const extra = pagination.columns;
  const loadingMore = pagination.loading;
  const error = pagination.error;
  const more = pagination.more;
  const items = boardItems(data.columns, Object.values(extra), (item) => item.key);
  const drag = useBoardDrag<SourcePipelineCard, SourcePipelineStage>({
    type: dragType,
    disabled: busy !== null,
    getKey: (item) => item.key,
    getTargets: (item) => item.retry_targets.map((target) => target.stage),
    onDrop: (item, stage) => {
      const target = item.retry_targets.find((target) => target.stage === stage);
      if (target) onRun(item, target);
    },
  });
  return (
    <KanbanBoard
      label="Source lifecycle pipeline"
      stageLabel="Source pipeline stage"
      stages={visible}
      labels={labels}
      counts={data.counts}
      selected={stage}
      onSelect={onStage}
      layoutKey={`${visible.join(",")}|${collapsed.join(",")}`}
    >
      {visible.map((value) => {
        const folded = collapsed.includes(value);
        const column = extra[value] ??
          data.columns.find((column) => column.stage === value) ?? {
            stage: value,
            total: data.counts[value] ?? 0,
            items: [],
            next_cursor: null,
          };
        const cards = items
          .filter((item) => item.stage === value)
          .sort((a, b) =>
            ["active", "paused", "rejected"].includes(value)
              ? 0
              : compareBoardItems(
                  { state: a.state, timestamp: a.created_at, key: a.key },
                  { state: b.state, timestamp: b.created_at, key: b.key },
                ),
          );
        const drop = drag.columnProps(value);
        return (
          <KanbanColumn
            key={value}
            id={`source-pipeline-${value}`}
            contentId={`source-pipeline-${value}-items`}
            label={labels[value]}
            total={column.total}
            countLabel={`${column.total} sources`}
            collapsed={folded}
            selected={stage === value}
            onCollapse={() => onCollapse(value)}
            {...drop}
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
                drag={drag.cardProps(item)}
              />
            ))}
            <BoardLoadMore
              available={column.next_cursor != null}
              loading={loadingMore === value}
              disabled={!!loadingMore}
              error={error === value ? "Could not load more sources. Try again." : undefined}
              onLoad={() => void more(column)}
            >
              Load more {labels[value].toLowerCase()} sources
            </BoardLoadMore>
          </KanbanColumn>
        );
      })}
    </KanbanBoard>
  );
}

function SourceCard({
  item,
  busy,
  onRun,
  onCandidate,
  drag,
}: {
  item: SourcePipelineCard;
  busy: boolean;
  onRun: (item: SourcePipelineCard, target?: RetryTarget) => void;
  onCandidate: (item: SourcePipelineCard) => void;
  drag: Pick<React.ComponentProps<"article">, "draggable" | "onDragStart" | "onDragEnd">;
}) {
  const status =
    sourceCardStatuses[
      item.stage === "active" || item.stage === "paused" || item.stage === "rejected"
        ? item.stage
        : item.state
    ] ?? sourceCardStatuses.blocked;
  const name = item.name || sourcePipelineHost(item);
  const titleClass =
    "rounded-sm text-sm font-medium leading-snug hover:underline focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <KanbanCard
      status={status}
      data-board-item-id={item.key}
      data-board-stage={item.stage}
      data-source-pipeline-kind={item.kind}
      data-source-pipeline-id={item.id}
      data-pipeline-state={item.state}
      {...drag}
      title={
        item.kind === "source" ? (
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
        )
      }
      actions={
        (item.can_advance || item.can_approve || !!item.retry_targets.length) && (
          <BoardCardActions label={`Source actions ${name}`} disabled={busy}>
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
          </BoardCardActions>
        )
      }
    >
      <p className="mt-1 truncate text-xs text-muted-foreground">{sourcePipelineHost(item)}</p>
    </KanbanCard>
  );
}
