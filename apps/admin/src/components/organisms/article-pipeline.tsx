"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { DropdownMenuItem } from "@devfeed/ui/dropdown-menu";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { EntityPicker } from "@/components/molecules/entity-picker";
import { RequestState } from "@/components/molecules/request-state";
import { SnapshotStatus } from "@/components/molecules/snapshot-status";
import { useRequest } from "@/lib/use-request";
import { useBoardRefreshInterval } from "@/lib/use-refresh-interval";
import { pipelineCardStatuses as cardStatuses } from "@/lib/pipeline-status";
import { ArticlePipelineSourceGroup } from "./article-pipeline-source-group";
import { useBoardColumns } from "@/lib/use-board-columns";
import { useAdmin } from "@/components/molecules/admin-session";
import { recordHref } from "@/lib/routes";
import { notify, notifyFailure } from "@/lib/notifications";
import {
  getArticlePipeline,
  getArticlePipelineColumn,
  retryArticlePipeline,
  pipelineLabels,
  pipelineStages,
  pipelineColumnsStorageKey,
  type PipelineBoard,
  type PipelineColumn,
  type PipelineArticle,
  type PipelineStage,
  type PipelineRetryTarget,
  type PipelineSourceGroup,
} from "@/lib/article-pipeline";

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

export function ArticlePipeline({ query }: { query: string }) {
  const router = useRouter();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const admin = useAdmin();
  const [retrying, setRetrying] = useState<string | null>(null);
  const retryPending = useRef(false);
  const columns = useBoardColumns(pipelineColumnsStorageKey, pipelineStages);
  const refreshSeconds = useBoardRefreshInterval();
  const load = useCallback((signal: AbortSignal) => getArticlePipeline(query, signal), [query]);
  const result = useRequest(`article-pipeline/${query}`, load, refreshSeconds * 1000, revision);
  const stage =
    columns.visible.find((value) => value === search.get("stage")) ??
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
    router[replace ? "replace" : "push"](`/content/articles?${next}`, { scroll: false });
  }
  async function retry(article: PipelineArticle, target: PipelineRetryTarget) {
    if (retryPending.current || !article.retry_targets?.includes(target)) return;
    retryPending.current = true;
    setRetrying(article.id);
    try {
      await retryArticlePipeline(
        article.id,
        target,
        article.editorial_revision,
        admin.csrf_token,
        article.jobs[target]?.id ?? null,
      );
      notify.success(
        article.stage === "published"
          ? `Unpublished and queued ${pipelineLabels[target]} retry`
          : `${pipelineLabels[target]} retry queued`,
      );
    } catch (cause) {
      notifyFailure(cause, "Could not retry article processing");
    } finally {
      retryPending.current = false;
      setRetrying(null);
      setRevision((value) => value + 1);
    }
  }
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <SearchField
          className="min-w-48 flex-1 sm:max-w-md"
          label="Search article pipeline"
          value={search.get("q") ?? ""}
          scopeKey={searchScope(query)}
          onSearch={(q) => change({ q }, true)}
        />
        <div className="w-full sm:w-60">
          <EntityPicker
            resource="sources"
            label="Source filter"
            value={search.get("source_id") ?? ""}
            onChange={(source_id) => change({ source_id })}
          />
        </div>
        <Select
          label="Pipeline status"
          className="w-full sm:w-48"
          value={search.get("needs_attention") === "true" ? "true" : ""}
          onChange={(needs_attention) => change({ needs_attention })}
          placeholder="All articles"
          clearLabel="All articles"
          options={[{ value: "true", label: "Needs attention" }]}
        />
        <Select
          label="Group cards"
          className="w-full sm:w-40"
          value={search.get("group_by") === "source" ? "source" : ""}
          onChange={(group_by) => change({ group_by })}
          placeholder="Articles"
          clearLabel="Articles"
          options={[{ value: "source", label: "Source" }]}
        />
        <BoardColumnsMenu
          stages={pipelineStages}
          labels={pipelineLabels}
          columns={columns}
          label="Pipeline columns"
        />
      </div>
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      <SnapshotStatus snapshot={result.data?.snapshot} checkedAt={result.data?.as_of} />
      {result.data && (
        <PipelineColumns
          key={query}
          data={result.data}
          query={query}
          stage={stage}
          onStage={(stage) => change({ stage })}
          visible={columns.visible}
          collapsed={columns.collapsed}
          onCollapse={columns.toggleCollapsed}
          refreshKey={revision}
          retrying={retrying}
          onRetry={(article, target) => void retry(article, target)}
        />
      )}
    </div>
  );
}

function PipelineColumns({
  data,
  query,
  stage,
  onStage,
  visible,
  collapsed,
  onCollapse,
  refreshKey,
  retrying,
  onRetry,
}: {
  data: PipelineBoard;
  query: string;
  stage: PipelineStage;
  onStage: (stage: PipelineStage) => void;
  visible: PipelineStage[];
  collapsed: PipelineStage[];
  onCollapse: (stage: PipelineStage) => void;
  refreshKey: number;
  retrying: string | null;
  onRetry: (article: PipelineArticle, target: PipelineRetryTarget) => void;
}) {
  const grouped = new URLSearchParams(query).get("group_by") === "source";
  const [groupLayout, setGroupLayout] = useState(0);
  const [, setGroupRevision] = useState(0);
  const onGroupArticlesChange = useCallback(() => setGroupRevision((value) => value + 1), []);
  const onGroupExpansionChange = useCallback(() => setGroupLayout((value) => value + 1), []);
  const pagination = useBoardPagination<PipelineBoard, PipelineStage, PipelineColumn>({
    snapshot: data,
    load: (column, signal) =>
      getArticlePipelineColumn(column.stage, query, column.next_cursor!, signal),
    merge: (column, next) => ({
      ...next,
      items: [...column.items, ...next.items],
      source_groups: [...(column.source_groups ?? []), ...(next.source_groups ?? [])],
    }),
    failureMessage: "Could not load more articles",
  });
  const extra = pagination.columns;
  const busy = pagination.loading;
  const error = pagination.error;
  const more = pagination.more;
  const articles = boardItems(data.columns, Object.values(extra), (item) => item.id);
  const drag = useBoardDrag<PipelineArticle, PipelineStage>({
    type: pipelineDragType,
    disabled: retrying !== null,
    getKey: (item) => item.id,
    getTargets: (item) => item.retry_targets ?? [],
    onDrop: (item, stage) => {
      const target = item.retry_targets?.find((target) => target === stage);
      if (target) onRetry(item, target);
    },
  });
  function renderArticle(item: PipelineArticle) {
    return (
      <PipelineCard
        key={item.id}
        article={item}
        showSource={!grouped}
        busy={retrying !== null}
        retrying={retrying === item.id}
        onRetry={onRetry}
        drag={drag.cardProps(item)}
      />
    );
  }
  return (
    <KanbanBoard
      label="Article publication pipeline"
      stageLabel="Pipeline stage"
      stages={visible}
      labels={pipelineLabels}
      counts={data.counts}
      selected={stage}
      onSelect={onStage}
      layoutKey={`${grouped}|${groupLayout}|${visible.join(",")}|${collapsed.join(",")}`}
    >
      {visible.map((value) => {
        const isCollapsed = collapsed.includes(value);
        const drop = drag.columnProps(value);
        const original = data.columns.find((column) => column.stage === value);
        const column = extra[value] ??
          original ?? {
            stage: value,
            total: data.counts[value] ?? 0,
            items: [],
            next_cursor: null,
          };
        const items = articles
          .filter((item) => item.stage === value)
          .sort((a, b) => {
            if (value === "published" || value === "rejected") return 0;
            return compareBoardItems(
              { state: a.state, timestamp: a.discovered_at, key: a.id },
              { state: b.state, timestamp: b.discovered_at, key: b.id },
            );
          });
        const groups = [
          ...new Map(
            (column.source_groups ?? []).map((group) => [group.source_id, group]),
          ).values(),
        ].sort((a, b) => sourcePriority(a) - sourcePriority(b));
        return (
          <KanbanColumn
            key={value}
            id={`pipeline-${value}`}
            contentId={`pipeline-${value}-articles`}
            label={pipelineLabels[value]}
            total={column.total}
            countLabel={`${column.total} articles`}
            collapsed={isCollapsed}
            selected={stage === value}
            onCollapse={() => onCollapse(value)}
            {...drop}
            title={
              drop.dropAllowed
                ? `${drag.item?.stage === "published" ? "Unpublish and retry" : "Retry"} ${pipelineLabels[value]}`
                : undefined
            }
          >
            {grouped
              ? groups.map((group) => (
                  <ArticlePipelineSourceGroup
                    key={group.source_id ?? "unknown"}
                    group={group}
                    stage={value}
                    query={query}
                    snapshot={data}
                    refreshKey={refreshKey}
                    renderArticle={renderArticle}
                    onArticlesChange={onGroupArticlesChange}
                    onExpansionChange={onGroupExpansionChange}
                  />
                ))
              : items.map(renderArticle)}
            {!(grouped ? groups.length : items.length) && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No articles in this stage.
              </p>
            )}
            <BoardLoadMore
              available={column.next_cursor != null}
              loading={busy === value}
              disabled={!!busy}
              error={error === value ? "Could not load more articles. Try again." : undefined}
              onLoad={() => void more(column)}
            >
              Load more {pipelineLabels[value].toLowerCase()} {grouped ? "sources" : "articles"}
            </BoardLoadMore>
          </KanbanColumn>
        );
      })}
    </KanbanBoard>
  );
}

const pipelineDragType = "application/x-devfeed-article-pipeline";

function sourcePriority(group: PipelineSourceGroup) {
  if (group.state_counts.running > 0) return 0;
  return group.state_counts.blocked > 0 || group.state_counts.retry_wait > 0 ? 1 : 2;
}

function PipelineCard({
  article,
  showSource,
  busy,
  retrying,
  onRetry,
  drag,
}: {
  article: PipelineArticle;
  showSource: boolean;
  busy: boolean;
  retrying: boolean;
  onRetry: (article: PipelineArticle, target: PipelineRetryTarget) => void;
  drag: Pick<React.ComponentProps<"article">, "draggable" | "onDragStart" | "onDragEnd">;
}) {
  const key =
    article.stage === "published" || article.stage === "rejected" ? article.stage : article.state;
  const status = cardStatuses[key as keyof typeof cardStatuses] ?? cardStatuses.waiting;
  const targets = article.retry_targets ?? [];
  return (
    <KanbanCard
      status={status}
      className={`space-y-2 ${targets.length && !busy ? "cursor-grab active:cursor-grabbing" : ""} ${retrying ? "opacity-60" : ""}`}
      data-board-item-id={article.id}
      data-board-stage={article.stage}
      data-pipeline-article-id={article.id}
      data-pipeline-stage={article.stage}
      data-pipeline-state={article.state}
      aria-busy={retrying || undefined}
      {...drag}
      title={
        <Link
          prefetch={false}
          href={recordHref({ resource: "articles", id: article.id })}
          className="hover:underline"
          draggable={false}
        >
          {article.title}
        </Link>
      }
      actions={
        targets.length > 0 && (
          <BoardCardActions label={`Move back ${article.title}`} disabled={busy}>
            {targets.map((target) => (
              <DropdownMenuItem key={target} onSelect={() => onRetry(article, target)}>
                {article.stage === "published" ? "Unpublish and retry" : "Retry"}{" "}
                {target === "analysis" ? "AI analysis" : pipelineLabels[target].toLowerCase()}
              </DropdownMenuItem>
            ))}
          </BoardCardActions>
        )
      }
    >
      {showSource && (
        <p className="break-words text-xs text-muted-foreground">
          {article.source_name || "Unknown source"}
        </p>
      )}
    </KanbanCard>
  );
}
