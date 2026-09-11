"use client";

import { DateTime } from "@/components/molecules/date-time";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, CircleAlert, FileText, Network, Rss, Sparkles, Tags } from "lucide-react";
import { Badge } from "@/components/atoms/badge";
import { Button } from "@/components/atoms/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { SummaryStrip } from "@/components/molecules/summary-strip";
import { OverviewCharts, TopicCoverage } from "@/components/organisms/overview-charts";
import { AutomationOverview } from "@/components/organisms/automation-overview";
import { adminOverview } from "@/lib/api/generated/admin";
import type { AdminOverview } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";
import { resourceHref } from "@/lib/routes";
import { usePolling } from "@/lib/use-polling";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { cn } from "@/lib/utils";

export function Overview({ initialData }: { initialData: AdminOverview }) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const refreshSeconds = useRefreshInterval();
  const request = useRef<AbortController | null>(null);
  const requestedDays = useRef(initialData.days);
  const failureNotified = useRef(false);
  useEffect(() => () => request.current?.abort(), []);

  const refresh = useCallback(async (days: number, manual = false, automaticSignal?: AbortSignal) => {
    if (automaticSignal && request.current) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    requestedDays.current = days;
    const cancel = () => {
      controller.abort();
      if (request.current === controller) { request.current = null; setLoading(false); }
    };
    automaticSignal?.addEventListener("abort", cancel, { once: true });
    setLoading(true);
    try {
      const next = await adminOverview({ days }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setData(next);
      setFailed(false);
      failureNotified.current = false;
      if (manual) notify.success("Overview refreshed");
    } catch (error) {
      if (controller.signal.aborted) return;
      setFailed(true);
      if (manual || !failureNotified.current || (error instanceof ApiError && error.status === 401)) {
        notifyFailure(error, "Could not refresh overview");
      }
      failureNotified.current = true;
    } finally {
      automaticSignal?.removeEventListener("abort", cancel);
      if (!controller.signal.aborted) { request.current = null; setLoading(false); }
    }
  }, []);

  usePolling(signal => refresh(requestedDays.current, false, signal), refreshSeconds * 1000);

  const queues = [
    { label: "Articles", count: data.articles_pending_review, href: `${resourceHref("articles")}?review_status=pending`, icon: FileText },
    { label: "Sources", count: data.sources_pending_review, href: `${resourceHref("sources")}?approval_status=pending`, icon: Rss },
    { label: "Topic proposals", count: data.topic_proposals_pending, href: `${resourceHref("topics")}?view=proposals&status=pending`, icon: Tags },
    { label: "Relationships", count: data.relationship_proposals_pending, href: `${resourceHref("topic-relations")}/proposals?status=pending`, icon: Network },
  ];
  const pending = queues.reduce((total, queue) => total + queue.count, 0);
  const ai = data.analysis;

  return <section className="space-y-5" aria-label="Application overview" aria-busy={loading}>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">Overview</h1><p className="mt-1 text-sm text-muted-foreground">Your feed, current work, and items to review.</p></div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border bg-muted/50 p-1" role="group" aria-label="Chart date range">
          {[7, 30].map(days => <Button key={days} variant={data.days === days ? "default" : "ghost"} size="sm" aria-pressed={data.days === days} disabled={loading} className="h-7 rounded-md px-3 text-xs" onClick={() => { if (days !== data.days) void refresh(days, false); }}>{days} days</Button>)}
        </div>
      </div>
    </div>

    {failed && <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm"><CircleAlert aria-hidden className="size-4 shrink-0 text-destructive" /><span>Could not update the overview. Showing the last successful snapshot.</span><Button variant="link" size="sm" className="h-auto p-0" disabled={loading} onClick={() => void refresh(requestedDays.current, true)}>Try again</Button></div>}

    <SummaryStrip items={[
      { label: "Published articles", value: data.articles_published, href: `${resourceHref("articles")}?publication_status=published` },
      { label: "Awaiting review", value: pending, href: "#review" },
      { label: "Active sources", value: data.sources_active, href: `${resourceHref("sources")}?approval_status=approved&enabled=true`, description: data.sources_failing ? `${data.sources_failing} with recent fetch failures` : undefined },
      { label: "Active topics", value: data.topics_active, href: `${resourceHref("topics")}?status=active` },
    ]} />

    <OverviewCharts data={data} />

    <div className="grid gap-4 lg:grid-cols-2">
      <Card id="review" className="scroll-mt-4 gap-4 shadow-none">
        <CardHeader className="gap-2 px-5 sm:px-6"><CardTitle><h2 className="flex items-center gap-2">Awaiting review {pending > 0 && <Badge variant="warning" className="tabular-nums">{pending.toLocaleString("en")}</Badge>}</h2></CardTitle>{!pending && <CardDescription>No items awaiting review.</CardDescription>}</CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-6 px-5 sm:grid-cols-2 sm:px-6">{queues.map(({ label, count, href, icon: Icon }) => <Link key={label} href={href} aria-label={`${label}: ${count.toLocaleString("en")} awaiting review`} className="group flex items-center gap-3 rounded-md py-3 outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring">
          <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" /><span className="text-sm">{label}</span><span className={cn("ml-auto text-sm tabular-nums", count ? "font-semibold" : "text-muted-foreground")}>{count.toLocaleString("en")}</span><ArrowRight aria-hidden className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
        </Link>)}</CardContent>
      </Card>
      <Card className="gap-4 shadow-none">
        <CardHeader className="gap-2 px-5 sm:px-6"><div className="flex items-center justify-between gap-3"><CardTitle><h2 className="flex items-center gap-2"><Sparkles aria-hidden className="size-4 text-chart-1" />AI analysis</h2></CardTitle><Link href={resourceHref("analysis-jobs")} className="inline-flex items-center gap-1 text-xs font-medium hover:underline">View jobs <ArrowUpRight aria-hidden className="size-3.5" /></Link></div></CardHeader>
        <CardContent className="space-y-4 px-5 sm:px-6">
          <div className="flex flex-wrap gap-4">
            <div><p className="mb-2 text-xs text-muted-foreground">In progress now</p><div className="flex flex-wrap gap-x-5 gap-y-2">
              <Link href={`${resourceHref("analysis-jobs")}?status=running`} className="text-sm hover:underline"><strong className="mr-1 font-semibold tabular-nums">{ai.running.toLocaleString("en")}</strong> running</Link>
              <Link href={`${resourceHref("analysis-jobs")}?status=queued`} className="text-sm text-muted-foreground hover:underline"><strong className="mr-1 font-medium text-foreground tabular-nums">{ai.queued.toLocaleString("en")}</strong> queued</Link>
            </div></div>

          </div>
          <div className="flex flex-wrap items-center gap-4 border-t pt-3 text-xs">
            <Link className="font-medium hover:underline" href="/workers">Workers <ArrowUpRight aria-hidden className="inline size-3" /></Link>
            <Link className="font-medium hover:underline" href="/queues">Queues <ArrowUpRight aria-hidden className="inline size-3" /></Link>
            {ai.failed > 0 && <Link className="font-medium text-destructive hover:underline" href={`${resourceHref("analysis-jobs")}?status=failed`}>Review failed jobs</Link>}
          </div>
        </CardContent>
      </Card>
    </div>
    {data.sources_failing > 0 && <p className="text-sm text-destructive">{data.sources_failing} source{data.sources_failing === 1 ? " needs" : "s need"} attention. <Link href={resourceHref("sources")} className="underline underline-offset-4">View sources</Link></p>}
    {data.automation && <AutomationOverview data={data.automation} onChange={() => void refresh(requestedDays.current)} />}
    <TopicCoverage data={data} />
    <p className="text-right text-xs text-muted-foreground" role="status">{loading ? "Updating overview…" : <>Updated <DateTime value={data.generated_at} /></>}</p>
  </section>;
}
