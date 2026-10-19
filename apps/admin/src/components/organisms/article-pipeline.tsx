"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronsLeft, ChevronsRight, Columns3, MoreHorizontal } from "lucide-react";
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
import { EntityPicker } from "@/components/molecules/entity-picker";
import { RequestState } from "@/components/molecules/request-state";
import { useRequest } from "@/lib/use-request";
import { useArticlePipelineInterval } from "@/lib/use-refresh-interval";
import { usePipelineMotion } from "@/lib/use-pipeline-motion";
import { pipelineCardStatuses as cardStatuses } from "@/lib/pipeline-status";
import { ArticlePipelineSourceGroup } from "./article-pipeline-source-group";
import { usePipelineColumns } from "@/lib/use-pipeline-columns";
import { useAdmin } from "@/components/molecules/admin-session";
import { recordHref } from "@/lib/routes";
import { notify, notifyFailure } from "@/lib/notifications";
import {
  getArticlePipeline,
  getArticlePipelineColumn,
  retryArticlePipeline,
  pipelineLabels,
  pipelineStages,
  type PipelineBoard,
  type PipelineColumn,
  type PipelineArticle,
  type PipelineStage,
  type PipelineRetryTarget,
  type PipelineSourceGroup,
} from "@/lib/article-pipeline";

export function ArticlePipeline({ query }: { query: string }) {
  const router = useRouter();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const admin = useAdmin();
  const [retrying, setRetrying] = useState<string | null>(null);
  const retryPending = useRef(false);
  const columns = usePipelineColumns();
  const refreshSeconds = useArticlePipelineInterval();
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
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="sm:ml-auto">
              <Columns3 aria-hidden="true" />
              Columns
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" aria-label="Pipeline columns" className="w-56 p-2">
            <p className="px-2 py-1.5 text-sm font-medium">Show columns</p>
            {pipelineStages.map((value) => {
              const checked = columns.visible.includes(value);
              return (
                <label
                  key={value}
                  className="flex cursor-pointer items-center gap-3 rounded px-2 py-2 text-sm hover:bg-muted has-disabled:cursor-default has-disabled:text-muted-foreground"
                >
                  <Input
                    type="checkbox"
                    checked={checked}
                    disabled={checked && columns.visible.length === 1}
                    onChange={(event) => columns.setVisible(value, event.target.checked)}
                  />
                  {pipelineLabels[value]}
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
  const motionRef = usePipelineMotion(
    `${grouped}|${groupLayout}|${visible.join(",")}|${collapsed.join(",")}`,
  );
  const [, setGroupRevision] = useState(0);
  const onGroupArticlesChange = useCallback(() => setGroupRevision((value) => value + 1), []);
  const onGroupExpansionChange = useCallback(() => setGroupLayout((value) => value + 1), []);
  const [additional, setAdditional] = useState<{
    snapshot: PipelineBoard;
    columns: Partial<Record<PipelineStage, PipelineColumn>>;
  }>();
  const [busy, setBusy] = useState<PipelineStage>();
  const [error, setError] = useState<{ stage: PipelineStage; message: string }>();
  const dragging = useRef<PipelineArticle | null>(null);
  const [dragged, setDragged] = useState<PipelineArticle | null>(null);
  const [dropStage, setDropStage] = useState<PipelineStage | null>(null);
  function clearDrag() {
    dragging.current = null;
    setDragged(null);
    setDropStage(null);
  }
  const current = useRef(data);
  useEffect(() => {
    current.current = data;
  }, [data]);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const extra = additional?.snapshot === data ? additional.columns : {};
  // A refreshed snapshot replaces old pages. Newer lane pages also replace stale copies of a moving card.
  const articles = new Map<string, PipelineArticle>();
  data.columns.forEach((column) => column.items.forEach((item) => articles.set(item.id, item)));
  Object.values(extra).forEach((column) =>
    column?.items.forEach((item) => articles.set(item.id, item)),
  );
  async function more(column: PipelineColumn) {
    if (!column.next_cursor || busy) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(column.stage);
    setError(undefined);
    const snapshot = data;
    try {
      const next = await getArticlePipelineColumn(
        column.stage,
        query,
        column.next_cursor,
        controller.signal,
      );
      if (!controller.signal.aborted && current.current === snapshot)
        setAdditional((previous) => ({
          snapshot,
          columns: {
            ...(previous?.snapshot === snapshot ? previous.columns : {}),
            [column.stage]: {
              ...next,
              items: [...column.items, ...next.items],
              source_groups: [...(column.source_groups ?? []), ...(next.source_groups ?? [])],
            },
          },
        }));
    } catch (cause) {
      if (!controller.signal.aborted) {
        notifyFailure(cause, "Could not load more articles");
        setError({ stage: column.stage, message: "Could not load more articles. Try again." });
      }
    } finally {
      if (request.current === controller) setBusy(undefined);
    }
  }
  function renderArticle(item: PipelineArticle) {
    return (
      <PipelineCard
        key={item.id}
        article={item}
        showSource={!grouped}
        busy={retrying !== null}
        retrying={retrying === item.id}
        onRetry={onRetry}
        onDragStart={(event) => {
          if (
            retrying ||
            !item.retry_targets?.length ||
            (event.target as HTMLElement).closest("button")
          ) {
            event.preventDefault();
            return;
          }
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData(pipelineDragType, item.id);
          event.dataTransfer.setData("text/plain", item.id);
          dragging.current = item;
          setDragged(item);
        }}
        onDragEnd={clearDrag}
      />
    );
  }
  return (
    <>
      <div className="lg:hidden">
        <Select
          label="Pipeline stage"
          value={stage}
          onChange={(value) => onStage(value as PipelineStage)}
          required
          options={visible.map((value) => ({
            value,
            label: `${pipelineLabels[value]} (${data.counts[value] ?? 0})`,
          }))}
        />
      </div>
      <div
        ref={motionRef}
        role="region"
        aria-label="Article publication pipeline"
        tabIndex={0}
        className="max-w-full overflow-x-auto rounded-lg focus-visible:outline-2 focus-visible:outline-ring"
      >
        <div className="grid gap-4 lg:flex lg:w-max lg:items-stretch">
          {visible.map((value) => {
            const isCollapsed = collapsed.includes(value);
            const canDrop =
              !retrying && (dragged?.retry_targets ?? []).some((target) => target === value);
            const original = data.columns.find((column) => column.stage === value);
            const column = extra[value] ??
              original ?? {
                stage: value,
                total: data.counts[value] ?? 0,
                items: [],
                next_cursor: null,
              };
            const items = [...articles.values()]
              .filter((item) => item.stage === value)
              .sort((a, b) => {
                if (value === "published" || value === "rejected") return 0;
                const running = Number(b.state === "running") - Number(a.state === "running");
                if (running) return running;
                const discovered = Date.parse(a.discovered_at) - Date.parse(b.discovered_at);
                if (discovered) return discovered;
                // Date.parse drops the database's submillisecond discovery order.
                const fractionA = (a.discovered_at.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
                const fractionB = (b.discovered_at.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
                return (
                  fractionA.localeCompare(fractionB) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
                );
              });
            const groups = [
              ...new Map(
                (column.source_groups ?? []).map((group) => [group.source_id, group]),
              ).values(),
            ].sort((a, b) => sourcePriority(a) - sourcePriority(b));
            return (
              <section
                key={value}
                aria-labelledby={`pipeline-${value}`}
                data-pipeline-collapsed={isCollapsed}
                data-pipeline-drop-target={canDrop || undefined}
                title={
                  canDrop
                    ? `${dragged?.stage === "published" ? "Unpublish and retry" : "Retry"} ${pipelineLabels[value]}`
                    : undefined
                }
                className={`min-w-0 rounded-lg border bg-muted/30 lg:shrink-0 ${isCollapsed ? "lg:w-12" : "lg:w-72"} ${stage === value ? "" : "hidden lg:block"} ${canDrop ? (dropStage === value ? "ring-2 ring-primary" : "ring-1 ring-primary/40") : ""}`}
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
                  const article = dragging.current;
                  if (
                    !article ||
                    retrying ||
                    event.dataTransfer.getData(pipelineDragType) !== article.id ||
                    !article.retry_targets?.some((target) => target === value)
                  )
                    return;
                  event.preventDefault();
                  clearDrag();
                  onRetry(article, value as PipelineRetryTarget);
                }}
              >
                <div
                  className={`flex select-none items-center gap-2 border-b px-4 py-3 ${isCollapsed ? "lg:h-full lg:min-h-40 lg:flex-col lg:border-b-0 lg:px-2" : ""}`}
                  onDoubleClick={(event) => {
                    if (
                      window.innerWidth >= 1024 &&
                      !(event.target as HTMLElement).closest("button")
                    )
                      onCollapse(value);
                  }}
                >
                  <h2
                    id={`pipeline-${value}`}
                    className={`min-w-0 flex-1 text-sm font-semibold ${isCollapsed ? "lg:order-2 lg:flex-none lg:[writing-mode:vertical-rl]" : ""}`}
                  >
                    {pipelineLabels[value]}
                  </h2>
                  <span
                    className={`rounded-full bg-background px-2 py-0.5 text-xs tabular-nums ${isCollapsed ? "lg:order-3" : ""}`}
                    aria-label={`${column.total} articles`}
                  >
                    {column.total.toLocaleString()}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className={`hidden size-6 shrink-0 lg:inline-flex ${isCollapsed ? "lg:order-1" : ""}`}
                    aria-label={`${isCollapsed ? "Expand" : "Collapse"} ${pipelineLabels[value]} column`}
                    aria-expanded={!isCollapsed}
                    aria-controls={`pipeline-${value}-articles`}
                    title={`${isCollapsed ? "Expand" : "Collapse"} column`}
                    onClick={() => onCollapse(value)}
                  >
                    {isCollapsed ? (
                      <ChevronsRight aria-hidden="true" className="size-3.5" />
                    ) : (
                      <ChevronsLeft aria-hidden="true" className="size-3.5" />
                    )}
                  </Button>
                </div>
                <div
                  id={`pipeline-${value}-articles`}
                  className={`space-y-3 p-3 ${isCollapsed ? "lg:hidden" : ""}`}
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
                  {error?.stage === value && (
                    <p role="alert" className="text-xs text-destructive">
                      {error.message}
                    </p>
                  )}
                  {column.next_cursor && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      disabled={!!busy}
                      loading={busy === value}
                      onClick={() => void more(column)}
                    >
                      Load more {pipelineLabels[value].toLowerCase()}{" "}
                      {grouped ? "sources" : "articles"}
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
  onDragStart,
  onDragEnd,
}: {
  article: PipelineArticle;
  showSource: boolean;
  busy: boolean;
  retrying: boolean;
  onRetry: (article: PipelineArticle, target: PipelineRetryTarget) => void;
  onDragStart: React.DragEventHandler<HTMLElement>;
  onDragEnd: React.DragEventHandler<HTMLElement>;
}) {
  const key =
    article.stage === "published" || article.stage === "rejected" ? article.stage : article.state;
  const status = cardStatuses[key as keyof typeof cardStatuses] ?? cardStatuses.waiting;
  const Icon = status.icon;
  const targets = article.retry_targets ?? [];
  return (
    <article
      className={`relative space-y-2 rounded-md border border-l-4 p-3 shadow-sm ${status.card} ${targets.length && !busy ? "cursor-grab active:cursor-grabbing" : ""} ${retrying ? "opacity-60" : ""}`}
      data-pipeline-article-id={article.id}
      data-pipeline-stage={article.stage}
      data-pipeline-state={article.state}
      aria-busy={retrying || undefined}
      draggable={targets.length > 0 && !busy}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <div className="flex items-start gap-2">
        <h3 className="min-w-0 flex-1 break-words text-sm font-medium leading-snug">
          <Link
            prefetch={false}
            href={recordHref({ resource: "articles", id: article.id })}
            className="hover:underline"
            draggable={false}
          >
            {article.title}
          </Link>
        </h3>
        <span
          role="img"
          aria-label={status.label}
          title={status.label}
          className={`mt-0.5 shrink-0 ${status.color}`}
        >
          <Icon aria-hidden="true" className="size-3.5" />
        </span>
        {targets.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="-my-1 -mr-1 size-6 shrink-0"
                aria-label={`Move back ${article.title}`}
                disabled={busy}
              >
                <MoreHorizontal aria-hidden="true" className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent aria-label={`Move back ${article.title}`}>
              {targets.map((target) => (
                <DropdownMenuItem key={target} onSelect={() => onRetry(article, target)}>
                  {article.stage === "published" ? "Unpublish and retry" : "Retry"}{" "}
                  {target === "analysis" ? "AI analysis" : pipelineLabels[target].toLowerCase()}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {showSource && (
        <p className="break-words text-xs text-muted-foreground">
          {article.source_name || "Unknown source"}
        </p>
      )}
    </article>
  );
}
