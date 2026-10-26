"use client";

import { useCallback, useId, useLayoutEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { RequestState } from "@/components/molecules/request-state";
import {
  getArticlePipelineSourceArticles,
  type PipelineArticle,
  type PipelineBoard,
  type PipelineColumn,
  type PipelineSourceGroup,
  type PipelineStage,
} from "@/lib/article-pipeline";
import { pipelineCardStatuses } from "@/lib/pipeline-status";
import { useRequest } from "@/lib/use-request";
import { useBoardPagination } from "@/lib/use-board-pagination";
import { useBoardRefreshInterval } from "@/lib/use-refresh-interval";

const stateOrder = ["running", "blocked", "retry_wait", "waiting", "ready", "complete"];

export function ArticlePipelineSourceGroup({
  group,
  stage,
  query,
  snapshot,
  refreshKey,
  renderArticle,
  onArticlesChange,
  onExpansionChange,
}: {
  group: PipelineSourceGroup;
  stage: PipelineStage;
  query: string;
  snapshot: PipelineBoard;
  refreshKey: number;
  renderArticle: (article: PipelineArticle) => ReactNode;
  onArticlesChange: () => void;
  onExpansionChange: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const name = group.source_name || "Unknown source";
  const states = stateOrder.filter((state) => group.state_counts[state] > 0);
  function statusFor(state: string) {
    const key = stage === "published" || stage === "rejected" ? stage : state;
    return (
      pipelineCardStatuses[key as keyof typeof pipelineCardStatuses] ?? pipelineCardStatuses.waiting
    );
  }
  const status = statusFor(states[0] ?? "waiting");
  return (
    <div
      role="group"
      aria-label={`${name} source group`}
      data-pipeline-source-id={group.source_id ?? "unknown"}
      data-pipeline-stage={stage}
      className={`rounded-md border border-l-4 shadow-sm ${status.card}`}
    >
      <button
        type="button"
        className="flex w-full items-start gap-2 rounded-sm p-3 text-left focus-visible:outline-2 focus-visible:outline-ring"
        aria-label={`${expanded ? "Collapse" : "Expand"} ${name} articles`}
        aria-expanded={expanded}
        aria-controls={id}
        aria-describedby={`${id}-total ${id}-states`}
        onClick={() => {
          setExpanded((value) => !value);
          onExpansionChange();
        }}
      >
        {expanded ? (
          <ChevronDown aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        )}
        <span className="min-w-0 flex-1 break-words text-sm font-medium leading-snug">{name}</span>
        <span
          id={`${id}-total`}
          className="rounded-full bg-background/80 px-2 py-0.5 text-xs tabular-nums"
          aria-label={`${group.total} articles from ${name}`}
        >
          {group.total.toLocaleString()}
        </span>
      </button>
      <div id={`${id}-states`} className="-mt-1 flex flex-wrap gap-x-3 gap-y-1 px-3 pb-3 pl-9">
        {states.map((state) => {
          const current = statusFor(state);
          return (
            <span
              key={state}
              aria-label={`${group.state_counts[state]} ${current.label}`}
              className={`inline-flex items-center gap-1 text-xs tabular-nums ${current.color}`}
            >
              <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
              {group.state_counts[state].toLocaleString()} {current.label.toLowerCase()}
            </span>
          );
        })}
      </div>
      <div id={id}>
        {expanded && (
          <SourceArticles
            group={group}
            stage={stage}
            query={query}
            snapshot={snapshot}
            refreshKey={refreshKey}
            renderArticle={renderArticle}
            onArticlesChange={onArticlesChange}
          />
        )}
      </div>
    </div>
  );
}

function SourceArticles({
  group,
  stage,
  query,
  snapshot,
  refreshKey,
  renderArticle,
  onArticlesChange,
}: {
  group: PipelineSourceGroup;
  stage: PipelineStage;
  query: string;
  snapshot: PipelineBoard;
  refreshKey: number;
  renderArticle: (article: PipelineArticle) => ReactNode;
  onArticlesChange: () => void;
}) {
  const [revision, setRevision] = useState(0);
  const refreshSeconds = useBoardRefreshInterval();
  const load = useCallback(
    (signal: AbortSignal) =>
      getArticlePipelineSourceArticles(stage, query, group.source_id, 0, signal),
    [stage, query, group.source_id],
  );
  const result = useRequest(
    `article-pipeline-source/${stage}/${group.source_id ?? "unknown"}/${query}`,
    load,
    refreshSeconds * 1000,
    refreshKey + revision,
  );
  // Later pages belong to both the board and this group's first-page response.
  const page = useMemo(() => ({ board: snapshot, first: result.data }), [snapshot, result.data]);
  const pagination = useBoardPagination<typeof page, PipelineStage, PipelineColumn>({
    snapshot: page,
    load: (column, signal) =>
      getArticlePipelineSourceArticles(stage, query, group.source_id, column.next_cursor!, signal),
    merge: (previous, next) => ({ ...next, items: [...previous.items, ...next.items] }),
    failureMessage: "Could not load more source articles",
  });
  const column = result.error ? undefined : (pagination.columns[stage] ?? result.data);
  const items = [...new Map(column?.items.map((article) => [article.id, article])).values()];
  const busy = pagination.loading === stage;
  useLayoutEffect(onArticlesChange, [column, result.error, onArticlesChange]);
  return (
    <div className="space-y-3 border-t p-2">
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((value) => value + 1)}
      />
      {items.map(renderArticle)}
      {column && !items.length && !result.error && (
        <p className="py-3 text-center text-xs text-muted-foreground">No articles in this stage.</p>
      )}
      {pagination.error === stage && (
        <p role="alert" className="text-xs text-destructive">
          Could not load more articles. Try again.
        </p>
      )}
      {column?.next_cursor && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          disabled={busy || result.refreshing || !!result.error}
          loading={busy}
          onClick={() => void pagination.more(column)}
        >
          Load more {group.source_name || "Unknown source"} articles
        </Button>
      )}
    </div>
  );
}
