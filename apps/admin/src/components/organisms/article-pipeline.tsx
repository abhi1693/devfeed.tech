"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CheckCheck,
  CircleAlert,
  CircleCheck,
  CircleX,
  Clock3,
  LoaderCircle,
  TimerReset,
} from "lucide-react";
import { Button } from "@devfeed/ui/button";
import { SearchField, searchScope } from "@/components/molecules/search-field";
import { Select } from "@/components/molecules/select";
import { EntityPicker } from "@/components/molecules/entity-picker";
import { RequestState } from "@/components/molecules/request-state";
import { useRequest } from "@/lib/use-request";
import { useArticlePipelineInterval } from "@/lib/use-refresh-interval";
import { usePipelineMotion } from "@/lib/use-pipeline-motion";
import { recordHref } from "@/lib/routes";
import { notifyFailure } from "@/lib/notifications";
import {
  getArticlePipeline,
  getArticlePipelineColumn,
  pipelineLabels,
  pipelineStages,
  type PipelineBoard,
  type PipelineColumn,
  type PipelineArticle,
  type PipelineStage,
} from "@/lib/article-pipeline";

export function ArticlePipeline({ query }: { query: string }) {
  const router = useRouter();
  const search = new URLSearchParams(query);
  const [revision, setRevision] = useState(0);
  const refreshSeconds = useArticlePipelineInterval();
  const load = useCallback((signal: AbortSignal) => getArticlePipeline(query, signal), [query]);
  const result = useRequest(`article-pipeline/${query}/${revision}`, load, refreshSeconds * 1000);
  const stage =
    pipelineStages.find((value) => value === search.get("stage")) ??
    result.data?.columns.find((column) => column.total > 0)?.stage ??
    "ingested";
  function change(values: Record<string, string>, replace = false) {
    const next = new URLSearchParams(query);
    next.set("view", "pipeline");
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    router[replace ? "replace" : "push"](`/content/articles?${next}`, { scroll: false });
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
}: {
  data: PipelineBoard;
  query: string;
  stage: PipelineStage;
  onStage: (stage: PipelineStage) => void;
}) {
  const motionRef = usePipelineMotion();
  const [additional, setAdditional] = useState<{
    snapshot: PipelineBoard;
    columns: Partial<Record<PipelineStage, PipelineColumn>>;
  }>();
  const [busy, setBusy] = useState<PipelineStage>();
  const [error, setError] = useState<{ stage: PipelineStage; message: string }>();
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
            [column.stage]: { ...next, items: [...column.items, ...next.items] },
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
  return (
    <>
      <div className="lg:hidden">
        <Select
          label="Pipeline stage"
          value={stage}
          onChange={(value) => onStage(value as PipelineStage)}
          required
          options={pipelineStages.map((value) => ({
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
        <div className="grid gap-4 lg:w-max lg:grid-flow-col lg:auto-cols-[18rem]">
          {pipelineStages.map((value) => {
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
            return (
              <section
                key={value}
                aria-labelledby={`pipeline-${value}`}
                className={`min-w-0 rounded-lg border bg-muted/30 ${stage === value ? "" : "hidden lg:block"}`}
              >
                <div className="flex items-center justify-between border-b px-4 py-3">
                  <h2 id={`pipeline-${value}`} className="text-sm font-semibold">
                    {pipelineLabels[value]}
                  </h2>
                  <span
                    className="rounded-full bg-background px-2 py-0.5 text-xs tabular-nums"
                    aria-label={`${column.total} articles`}
                  >
                    {column.total.toLocaleString()}
                  </span>
                </div>
                <div className="space-y-3 p-3">
                  {items.map((item) => (
                    <PipelineCard key={item.id} article={item} />
                  ))}
                  {!items.length && (
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
                      Load more {pipelineLabels[value].toLowerCase()} articles
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

const cardStatuses = {
  waiting: {
    label: "Waiting",
    icon: Clock3,
    color: "text-amber-700 dark:text-amber-300",
    card: "border-amber-200 border-l-amber-400 bg-amber-50/70 dark:border-amber-900 dark:border-l-amber-500 dark:bg-amber-950/25",
  },
  running: {
    label: "Running",
    icon: LoaderCircle,
    color: "text-blue-700 dark:text-blue-300",
    card: "border-blue-200 border-l-blue-400 bg-blue-50/70 dark:border-blue-900 dark:border-l-blue-500 dark:bg-blue-950/25",
  },
  retry_wait: {
    label: "Waiting to retry",
    icon: TimerReset,
    color: "text-violet-700 dark:text-violet-300",
    card: "border-violet-200 border-l-violet-400 bg-violet-50/70 dark:border-violet-900 dark:border-l-violet-500 dark:bg-violet-950/25",
  },
  blocked: {
    label: "Blocked",
    icon: CircleAlert,
    color: "text-rose-700 dark:text-rose-300",
    card: "border-rose-200 border-l-rose-400 bg-rose-50/70 dark:border-rose-900 dark:border-l-rose-500 dark:bg-rose-950/25",
  },
  ready: {
    label: "Ready",
    icon: CircleCheck,
    color: "text-emerald-700 dark:text-emerald-300",
    card: "border-emerald-200 border-l-emerald-400 bg-emerald-50/70 dark:border-emerald-900 dark:border-l-emerald-500 dark:bg-emerald-950/25",
  },
  complete: {
    label: "Complete",
    icon: CheckCheck,
    color: "text-slate-600 dark:text-slate-300",
    card: "border-slate-200 border-l-slate-400 bg-slate-50/70 dark:border-slate-800 dark:border-l-slate-500 dark:bg-slate-950/25",
  },
  published: {
    label: "Published",
    icon: CheckCheck,
    color: "text-green-700 dark:text-green-300",
    card: "border-green-200 border-l-green-400 bg-green-50/70 dark:border-green-900 dark:border-l-green-500 dark:bg-green-950/25",
  },
  rejected: {
    label: "Rejected",
    icon: CircleX,
    color: "text-rose-700 dark:text-rose-300",
    card: "border-rose-200 border-l-rose-400 bg-rose-50/70 dark:border-rose-900 dark:border-l-rose-500 dark:bg-rose-950/25",
  },
};

function PipelineCard({ article }: { article: PipelineArticle }) {
  const key =
    article.stage === "published" || article.stage === "rejected" ? article.stage : article.state;
  const status = cardStatuses[key as keyof typeof cardStatuses] ?? cardStatuses.waiting;
  const Icon = status.icon;
  return (
    <article
      className={`relative space-y-2 rounded-md border border-l-4 p-3 shadow-sm ${status.card}`}
      data-pipeline-article-id={article.id}
      data-pipeline-stage={article.stage}
      data-pipeline-state={article.state}
    >
      <div className="flex items-start gap-2">
        <h3 className="min-w-0 flex-1 break-words text-sm font-medium leading-snug">
          <Link
            prefetch={false}
            href={recordHref({ resource: "articles", id: article.id })}
            className="hover:underline"
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
      </div>
      <p className="break-words text-xs text-muted-foreground">
        {article.source_name || "Unknown source"}
      </p>
    </article>
  );
}
