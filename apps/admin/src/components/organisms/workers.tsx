"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/atoms/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/atoms/table";
import { DateTime } from "@/components/molecules/date-time";
import { SummaryStrip } from "@/components/molecules/summary-strip";
import { PageHeading } from "@/components/molecules/page-heading";
import { Select } from "@/components/molecules/select";
import { StatusBadge } from "@/components/molecules/status-badge";
import { JobLogs } from "@/components/organisms/job-logs";
import { adminWorkerGet, adminWorkersSnapshot } from "@/lib/api/generated/admin";
import type { WorkerJob, WorkerOut } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { humanize, type Resource } from "@/lib/resources";
import { recordHref } from "@/lib/routes";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { useRequest } from "@/lib/use-request";

const linkClass = "font-medium hover:underline underline-offset-4";
const queueNames = ["ingestion", "analysis", "relationships", "notifications"];
const resourcesByKind: Record<string, Resource> = {
  ingestion: "ingestion-jobs", "article-enrichment": "article-jobs", images: "image-jobs",
  "source-enrichment": "source-jobs", analysis: "analysis-jobs", "topic-analysis": "analysis-jobs",
  "research-verification": "analysis-jobs", notifications: "notification-jobs",
};
const queueLinks: Record<string, { label: string; href: string }[]> = {
  ingestion: [{ label: "Feed ingestion", href: "/jobs/ingestion" }, { label: "Enrichment", href: "/jobs/enrichment" }],
  analysis: [{ label: "AI analysis runs", href: "/jobs/analysis" }, { label: "Topic proposals", href: "/taxonomy/topics/proposals" }],
  relationships: [{ label: "Research runs", href: "/jobs/analysis/topics" }, { label: "Relationship proposals", href: "/taxonomy/relationships/proposals" }],
  notifications: [{ label: "Deliveries", href: "/jobs/notifications" }],
};

function duration(seconds?: number | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}

function jobHref(job: WorkerJob, logs = false) {
  const resource = job.kind ? resourcesByKind[job.kind] : undefined;
  return resource && job.id ? recordHref(resource, { id: job.id, kind: job.kind === "research-verification" ? "topic-analysis" : job.kind }, logs ? "logs" : "details") : undefined;
}

function targetHref(job: WorkerJob) {
  if (job.proposal_id) return `/taxonomy/topics/proposals/${job.proposal_id}`;
  if (job.topic_id) return `/taxonomy/topics/${job.topic_id}`;
  if (job.article_id) return `/content/articles/${job.article_id}`;
  if (job.source_id) return `/content/sources/${job.source_id}`;
}

function JobLink({ job }: { job: WorkerJob }) {
  const href = jobHref(job);
  if (!href) return <span>Execution {job.rq_id.slice(0, 8)} · details unavailable</span>;
  return <div className="space-y-1"><Link href={href} className={linkClass}>{job.target_name ?? `Run ${job.id?.slice(0, 8)}`}</Link><p className="text-xs text-muted-foreground">{humanize((job.kind ?? "Job").replaceAll("-", " "))} · {duration(job.elapsed_seconds)}</p></div>;
}

function Refresh({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return <Button variant="outline" size="sm" onClick={onClick} disabled={loading}><RefreshCw aria-hidden size={14} />Refresh</Button>;
}

function ReadError({ error, stale }: { error?: Error; stale: boolean }) {
  if (!error) return null;
  return <p role="alert" className="rounded-lg border border-destructive/25 bg-destructive/5 p-4 text-sm">{error instanceof ApiError && error.status === 404 ? error.message : "Worker telemetry is unavailable."} {stale ? "Showing the last successful snapshot; it may be out of date." : "Refresh to try again."}</p>;
}

function useSnapshot() {
  const [revision, setRevision] = useState(0);
  const seconds = useRefreshInterval();
  const load = useCallback((signal: AbortSignal) => { void revision; return adminWorkersSnapshot({ signal }); }, [revision]);
  return { ...useRequest("workers", load, seconds * 1000), seconds, refresh: () => setRevision(value => value + 1) };
}

function workerLabel(worker: WorkerOut) {
  if (worker.name.length <= 28) return worker.name;
  return `${worker.role === "ai" ? "AI" : worker.role === "mixed" ? "Mixed" : "Background"} · ${worker.name.slice(0, 8)}`;
}

function WorkerTable({ workers }: { workers: WorkerOut[] }) {
  return <div className="overflow-hidden rounded-lg border bg-card"><Table aria-label="Workers"><TableHeader className="bg-muted/40"><TableRow>{["Worker", "Status", "Current work", "Completed", "Failed"].map(label => <TableHead className="px-4" key={label}>{label}</TableHead>)}</TableRow></TableHeader>
    <TableBody>{workers.map(worker => <TableRow key={worker.name}>
      <TableCell className="px-4 py-3"><Link href={`/workers/${encodeURIComponent(worker.name)}`} title={worker.name} className={linkClass}>{workerLabel(worker)}</Link></TableCell>
      <TableCell className="px-4"><StatusBadge value={worker.state} /></TableCell>
      <TableCell className="max-w-80 whitespace-normal px-4">{worker.current_job ? <JobLink job={worker.current_job} /> : <span className="text-muted-foreground">{worker.state === "busy" ? "Picking up work…" : worker.state === "suspended" ? "Paused" : worker.registered ? "Ready for work" : "Offline"}</span>}</TableCell>
      <TableCell className="px-4 tabular-nums">{worker.completed_executions.toLocaleString("en")}</TableCell>
      <TableCell className="px-4 tabular-nums">{worker.failed_executions ? <span className="text-destructive">{worker.failed_executions.toLocaleString("en")}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
    </TableRow>)}</TableBody></Table></div>;
}

export function WorkersOverview() {
  const { data, error, loading, refreshing, refresh, seconds } = useSnapshot();
  const [query, setQuery] = useState("");
  const [queue, setQueue] = useState("");
  const [state, setState] = useState("");
  const workers = data?.workers ?? [];
  const visible = workers.filter(worker => (!queue || worker.queues.includes(queue)) && (!state || worker.state === state) && `${workerLabel(worker)} ${worker.name} ${worker.hostname ?? ""} ${worker.current_job?.target_name ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="space-y-6" aria-busy={loading || refreshing}>
    <PageHeading title="Workers" description="See what is running and open a worker for details."><Link href="/queues" className={linkClass}>Queues</Link><Refresh onClick={refresh} loading={loading || refreshing} /></PageHeading>
    <ReadError error={error} stale={!!data} />
    {loading && <p role="status">Loading workers…</p>}
    {data && <>
      <SummaryStrip items={[
        { label: "Workers", value: workers.filter(w => w.registered).length },
        { label: "Busy", value: workers.filter(w => w.state === "busy").length },
        { label: "Idle", value: workers.filter(w => w.state === "idle").length },
        { label: "Suspended", value: workers.filter(w => w.state === "suspended").length },
      ]} />
      {data.ai_cooldown_seconds > 0 && <p role="status" className="rounded-lg border p-4 text-sm">New AI work is waiting for a provider cooldown: {duration(data.ai_cooldown_seconds)} remaining. Work already running can finish.</p>}
      <div className="flex flex-wrap gap-3"><Input aria-label="Search workers" placeholder="Search worker, host, or current subject…" className="max-w-md" value={query} onChange={event => setQuery(event.target.value)} /><Select label="Worker queue" value={queue} onChange={setQueue} clearLabel="All queues" placeholder="All queues" options={queueNames.map(value => ({ value, label: humanize(value) }))} /><Select label="Worker status" value={state} onChange={setState} clearLabel="All statuses" placeholder="All statuses" options={["busy", "idle", "suspended", "started", "offline", "unknown"].map(value => ({ value, label: humanize(value) }))} /></div>
      {visible.length ? <WorkerTable workers={visible} /> : <p className="rounded-lg border p-6 text-sm">{workers.length ? "No workers match these filters." : "No workers are registered. Check that the worker services are running."}</p>}
      <p className="text-xs text-muted-foreground">Counts cover this worker session. Open a run to check its result.</p>
      <p className="text-xs text-muted-foreground" role="status">Updated <DateTime value={data.generated_at} /> · {seconds ? `Refreshes every ${seconds}s` : "Automatic refresh is off"}</p>
    </>}
  </section>;
}

export function WorkerDetails({ name }: { name: string }) {
  const seconds = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback((signal: AbortSignal) => { void revision; return adminWorkerGet(name, { signal }); }, [name, revision]);
  const { data: worker, loading, error, refreshing } = useRequest(`worker/${name}`, load, seconds * 1000);
  const job = worker?.current_job;
  const href = job && jobHref(job);
  const target = job && targetHref(job);
  return <section className="space-y-6" aria-busy={loading || refreshing}>
    <PageHeading title={worker ? workerLabel(worker) : name.length > 28 ? "Worker" : name} browserTitle={`${worker ? workerLabel(worker) : "Worker"} · Workers`} trail={[{ label: "Workers", href: "/workers" }]} ><Refresh onClick={() => setRevision(value => value + 1)} loading={loading || refreshing} /></PageHeading>
    <ReadError error={error} stale={!!worker} />
    {loading && <p role="status">Loading worker…</p>}
    {worker && <>
      <div className="flex flex-wrap items-center gap-4 text-sm"><StatusBadge value={worker.state} /><span className="text-muted-foreground">{worker.role === "ai" ? "AI" : humanize(worker.role)} · {duration(worker.uptime_seconds)} uptime</span></div>
      <Card><CardHeader><CardTitle>Current work</CardTitle></CardHeader><CardContent className="space-y-4">{job ? <>
        <JobLink job={job} /><div className="flex flex-wrap gap-3"><StatusBadge value={job.status} />{job.outcome && <StatusBadge value={job.outcome} />}{href && <Link href={href} className={linkClass}>Run details and saved results</Link>}{target && <Link href={target} className={linkClass}>Open {job.proposal_id ? "topic proposal" : "subject"}</Link>}{jobHref(job, true) && <Link href={jobHref(job, true)!} className={linkClass}>Job logs</Link>}</div>
        {job.kind === "research-verification" && <p className="text-sm text-muted-foreground">Checking evidence before approval.</p>}
      </> : <p className="text-sm text-muted-foreground">{worker.state === "suspended" ? "This worker is suspended. Check AI connection status and the queue cooldown before expecting new work." : "Ready for the next job."}</p>}</CardContent></Card>
      {job?.id && href && <details className="rounded-lg border bg-card"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">Live logs</summary><div className="border-t p-4"><JobLogs kind={(job.kind === "research-verification" ? "topic-analysis" : job.kind) as Parameters<typeof JobLogs>[0]["kind"]} id={job.id} followExecution={job.kind === "research-verification" && worker.registered && worker.state === "busy" && !error} /></div></details>}
      <details className="rounded-lg border bg-card"><summary className="cursor-pointer px-5 py-4 text-sm font-medium">Technical details</summary><div className="space-y-4 border-t px-5 py-4"><p className="break-all font-mono text-xs text-muted-foreground">{worker.name}</p>      <p className="mb-4 text-xs text-muted-foreground">Queues: {worker.queues.map(humanize).join(", ")}</p><dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">{[
        ["Host", worker.hostname ?? "Unknown"], ["Process", worker.pid ?? "Unknown"], ["Uptime", duration(worker.uptime_seconds)], ["Heartbeat age", duration(worker.heartbeat_age_seconds)],
        ["Completed executions", worker.completed_executions], ["Failed executions", worker.failed_executions], ["Time processing", duration(worker.working_seconds)], ["Registration expires in", duration(Math.max(0, worker.registration_ttl_seconds))],
      ].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 break-all font-medium">{value}</dd></div>)}</dl>{worker.last_heartbeat && <p className="mt-4 text-xs text-muted-foreground">Last heartbeat <DateTime value={worker.last_heartbeat} /></p>}
<p className="text-xs text-muted-foreground">Execution counters reset with each registration. Logs have limited retention.</p></div></details>
    </>}
  </section>;
}

export function QueuesOverview() {
  const { data, error, loading, refreshing, refresh } = useSnapshot();
  return <section className="space-y-6" aria-busy={loading || refreshing}>
    <PageHeading title="Queues" description="Work waiting to be picked up, and what is running now."><Link href="/workers" className={linkClass}>Workers</Link><Refresh onClick={refresh} loading={loading || refreshing} /></PageHeading>
    <ReadError error={error} stale={!!data} />{loading && <p role="status">Loading queues…</p>}
    {data && <>
      {data.ai_cooldown_seconds > 0 && <p role="status" className="rounded-lg border p-4 text-sm">AI provider cooldown: {duration(data.ai_cooldown_seconds)} remaining.</p>}
      <div className="grid gap-5 xl:grid-cols-2">{data.queues.map(queue => <Card key={queue.name} id={queue.name} className="scroll-mt-6"><CardHeader><CardTitle>{humanize(queue.name)}</CardTitle><CardDescription>{queue.registered_workers} workers assigned</CardDescription></CardHeader><CardContent className="space-y-5">
        {!!queue.queued && !queue.registered_workers && <p role="status" className="text-sm text-destructive">Waiting work has no registered workers.</p>}
        <dl className="grid grid-cols-3 gap-4">{[["Waiting", queue.queued], ["Running", queue.running], ["Failed", queue.failed]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{Number(value).toLocaleString("en")}</dd></div>)}</dl>
        <details className="border-t pt-3"><summary className="cursor-pointer text-xs text-muted-foreground">More details</summary><dl className="mt-3 grid grid-cols-3 gap-4">{[["Dispatched", queue.dispatched], ["Succeeded", queue.succeeded], ["Review required", queue.review_required]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-sm tabular-nums">{Number(value).toLocaleString("en")}</dd></div>)}</dl><p className="mt-3 text-xs text-muted-foreground">Oldest waiting job: {queue.oldest_queued_at ? <DateTime value={queue.oldest_queued_at} /> : "None"}</p></details>
        <div className="flex flex-wrap gap-4 text-sm">{queueLinks[queue.name]?.map(link => <Link key={link.href} href={link.href} className={linkClass}>{link.label}</Link>)}</div>
      </CardContent></Card>)}</div>
      <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">About these counts</summary><p className="mt-2 max-w-3xl leading-5">Waiting includes scheduled retries. Dispatched is the current Redis backlog and can include duplicate attempts. Succeeded and failed are retained database run totals; review required counts completed verification runs, not failed analysis. Shared workers appear under each assigned queue.</p></details>
      <p className="text-xs text-muted-foreground" role="status">Updated <DateTime value={data.generated_at} /></p>
    </>}
  </section>;
}
