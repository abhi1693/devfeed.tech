"use client";

import { DateTime } from "@/components/molecules/date-time";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, CheckCheck, CircleAlert, FileText, Inbox, Network, Rss, Sparkles, Tags } from "lucide-react";
import { Badge } from "@/components/atoms/badge";
import { Button } from "@/components/atoms/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { Metric } from "@/components/molecules/metric";
import { OverviewCharts } from "@/components/organisms/overview-charts";
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
    { label: "Topic proposals", count: data.topic_proposals_pending, href: `${resourceHref("topics")}/proposals?status=pending`, icon: Tags },
    { label: "Relationships", count: data.relationship_proposals_pending, href: `${resourceHref("topic-relations")}/proposals?status=pending`, icon: Network },
  ];
  const pending = queues.reduce((total, queue) => total + queue.count, 0);
  const ai = data.analysis;

  return <section className="space-y-5" aria-label="Application overview" aria-busy={loading}>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight">Overview</h1><p className="mt-1 text-sm text-muted-foreground">A pulse on your feed, from discovery to publication.</p></div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border bg-muted/50 p-1" role="group" aria-label="Chart date range">
          {[7, 30].map(days => <Button key={days} variant="ghost" size="sm" aria-pressed={data.days === days} disabled={loading} className={cn("h-7 rounded-md px-3 text-xs", data.days === days && "bg-card shadow-sm")} onClick={() => { if (days !== data.days) void refresh(days, false); }}>{days} days</Button>)}
        </div>
      </div>
    </div>

    {failed && <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm"><CircleAlert aria-hidden className="size-4 shrink-0 text-destructive" /><span>Could not update the overview. Showing the last successful snapshot.</span><Button variant="link" size="sm" className="h-auto p-0" disabled={loading} onClick={() => void refresh(requestedDays.current, true)}>Try again</Button></div>}

    <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
      <Metric label="Published articles" value={data.articles_published} description="Currently live in your feed" icon={<FileText />} href={`${resourceHref("articles")}?publication_status=published`} />
      <Metric label="Articles to review" value={data.articles_pending_review} description="Waiting for an editorial decision" icon={<Inbox />} href={`${resourceHref("articles")}?review_status=pending`} />
      <Metric label="Active sources" value={data.sources_active} description={data.sources_failing ? `${data.sources_failing.toLocaleString("en")} with recent fetch failures` : "Approved and enabled for fetching"} icon={<Rss />} href={`${resourceHref("sources")}?approval_status=approved&enabled=true`} />
      <Metric label="Active topics" value={data.topics_active} description="Approved topics in your catalog" icon={<Tags />} href={`${resourceHref("topics")}?status=active`} />
    </div>

    <OverviewCharts data={data} />
    {data.automation && <AutomationOverview data={data.automation} onChange={() => void refresh(requestedDays.current)} />}

    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="gap-4 shadow-none">
        <CardHeader className="gap-2 px-5 sm:px-6"><CardTitle><h2 className="flex items-center gap-2">Awaiting review {pending > 0 && <Badge variant="warning" className="tabular-nums">{pending.toLocaleString("en")}</Badge>}</h2></CardTitle><CardDescription>{pending ? "Your decisions keep the feed and catalog curated." : "You’re all caught up. New submissions will appear here."}</CardDescription></CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-6 px-5 sm:grid-cols-2 sm:px-6">{queues.map(({ label, count, href, icon: Icon }) => <Link key={label} href={href} aria-label={`${label}: ${count.toLocaleString("en")} awaiting review`} className="group flex items-center gap-3 rounded-md py-3 outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring">
          <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" /><span className="text-sm">{label}</span><span className={cn("ml-auto text-sm tabular-nums", count ? "font-semibold" : "text-muted-foreground")}>{count.toLocaleString("en")}</span><ArrowRight aria-hidden className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
        </Link>)}</CardContent>
      </Card>
      <Card className="gap-4 shadow-none">
        <CardHeader className="gap-2 px-5 sm:px-6"><div className="flex items-center justify-between gap-3"><CardTitle><h2 className="flex items-center gap-2"><Sparkles aria-hidden className="size-4 text-chart-1" />AI analysis</h2></CardTitle><Link href={resourceHref("analysis-jobs")} className="inline-flex items-center gap-1 text-xs font-medium hover:underline">View jobs <ArrowUpRight aria-hidden className="size-3.5" /></Link></div><CardDescription>Article analysis, topic enrichment, and relationships.</CardDescription></CardHeader>
        <CardContent className="space-y-4 px-5 sm:px-6">
          <div className="grid grid-cols-2 gap-4">
            <div><p className="mb-2 text-xs text-muted-foreground">In progress now</p><div className="flex flex-wrap gap-x-5 gap-y-2">
              <Link href={`${resourceHref("analysis-jobs")}?status=running`} className="text-sm hover:underline"><strong className="mr-1 font-semibold tabular-nums">{ai.running.toLocaleString("en")}</strong> running</Link>
              <Link href={`${resourceHref("analysis-jobs")}?status=queued`} className="text-sm text-muted-foreground hover:underline"><strong className="mr-1 font-medium text-foreground tabular-nums">{ai.queued.toLocaleString("en")}</strong> queued</Link>
            </div></div>
            <div className="border-l pl-4"><p className="mb-2 text-xs text-muted-foreground">Finished in {data.days} days</p><div className="flex flex-wrap gap-x-5 gap-y-2 text-sm"><span><strong className="mr-1 font-semibold tabular-nums">{ai.succeeded.toLocaleString("en")}</strong> succeeded</span><span className={ai.failed ? "text-destructive" : "text-muted-foreground"}><strong className="mr-1 font-medium tabular-nums">{ai.failed.toLocaleString("en")}</strong> failed</span></div></div>
          </div>
          <div className="flex items-start gap-2 border-t pt-3 text-xs leading-5 text-muted-foreground">{ai.failed ? <><CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-destructive" /><span>Some runs need a closer look. <Link className="font-medium text-foreground underline underline-offset-4" href={`${resourceHref("analysis-jobs")}?status=failed`}>Review failed jobs</Link></span></> : <><CheckCheck aria-hidden className="mt-0.5 size-3.5 shrink-0" /><span>{ai.succeeded ? "Completed analysis follows your review and source publication policies." : "Completed runs will appear here as analysis finishes."}</span></>}</div>
        </CardContent>
      </Card>
    </div>
    <p className="text-right text-xs text-muted-foreground" role="status">{loading ? "Updating overview…" : <>Updated <DateTime value={data.generated_at} /></>}</p>
  </section>;
}
