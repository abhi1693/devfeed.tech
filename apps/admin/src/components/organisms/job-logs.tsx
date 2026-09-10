"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { usePolling } from "@/lib/use-polling";
import { Select } from "@/components/molecules/select";
import { InfoPanel } from "@/components/molecules/info-panel";
import { JobLogLine } from "@/components/molecules/job-log-entry";
import { adminJobLogs } from "@/lib/api/generated/admin";
import type { AdminJobLogs, JobLogEntry } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { StatusBadge } from "@/components/molecules/status-badge";
import { notifyFailure } from "@/lib/notifications";

type Props = { kind: Parameters<typeof adminJobLogs>[0]; id: string; followExecution?: boolean };
type LogState = { page?: AdminJobLogs; items: JobLogEntry[]; error?: string; unreadable: number; trimmed: boolean };

export function JobLogs(props: Props) {
  // Navigating directly between runs must discard the previous run's cursor/data.
  return <LogViewer key={`${props.kind}/${props.id}`} {...props} />;
}

function LogViewer({ kind, id, followExecution = false }: Props) {
  const [state, setState] = useState<LogState>({ items: [], unreadable: 0, trimmed: false });
  const refreshSeconds = useRefreshInterval();
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");
  const failed = useRef(false);
  const progress = useRef({ cursor: undefined as string | undefined, items: [] as JobLogEntry[], terminalPolls: 0, unreadable: 0, trimmed: false, blocked: false });
  const pending = useRef<AbortSignal | null>(null);
  const poll = useCallback(async (signal: AbortSignal, automatic = false) => {
    const current = progress.current;
    if (automatic && (current.blocked || (!followExecution && current.terminalPolls >= 3) || (pending.current && !pending.current.aborted))) return;
    pending.current = signal;
    setLoading(true);
    try {
      // Drain the current batch using its cursor, including completed runs.
      // Interval changes retain the cursor, loaded lines and local filters.
      let more = true;
      while (more && !signal.aborted) {
        const page = await adminJobLogs(kind, id, { after: current.cursor, limit: 200 }, { signal });
        if (signal.aborted) return;
        failed.current = false;
        const merged = new Map(current.items.map(entry => [entry.id, entry]));
        page.items.forEach(entry => merged.set(entry.id, entry));
        current.trimmed ||= page.truncated || merged.size > page.max_entries;
        current.items = Array.from(merged.values()).slice(-page.max_entries);
        current.unreadable += page.unreadable_entries;
        current.cursor = page.next_cursor ?? current.cursor;
        current.terminalPolls = !page.has_more && ["succeeded", "failed"].includes(page.job_status) ? current.terminalPolls + 1 : 0;
        setState({ page, items: current.items, unreadable: current.unreadable, trimmed: current.trimmed });
        more = page.has_more;
      }
    } catch (error) {
      if (signal.aborted) return;
      current.blocked = error instanceof ApiError && [401, 403, 404].includes(error.status);
      if (error instanceof ApiError && error.status === 401) { returnToLogin(); return; }
      if (!failed.current) notifyFailure(error, "Could not load runtime logs", `job-logs-${kind}-${id}`);
      failed.current = true;
      setState(previous => ({ ...previous, error: error instanceof ApiError && error.status === 404 ? "This run no longer exists." : "Could not load runtime logs. Retry or check the worker console." }));
    } finally {
      if (pending.current === signal) { pending.current = null; setLoading(false); }
    }
  }, [kind, id, followExecution]);
  useEffect(() => {
    const abort = new AbortController();
    progress.current.blocked = false;
    progress.current.terminalPolls = 0;
    void poll(abort.signal);
    return () => abort.abort();
  }, [poll, refresh]);
  usePolling(signal => poll(signal, true), refreshSeconds * 1000, `${kind}/${id}`);

  const shown = state.items.filter(entry => (level === "all" || (level === "problems" ? ["WARNING", "ERROR", "CRITICAL"].includes(entry.level) : entry.level === level)) && `${entry.message} ${JSON.stringify(entry.fields)}`.toLowerCase().includes(query.toLowerCase()));
  const page = state.page;
  function download() {
    const text = state.items.map(entry => `${entry.timestamp} ${entry.level} ${entry.message}\n  ${JSON.stringify(entry.fields)}`).join("\n");
    const url = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${kind}-${id}.log`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return <InfoPanel title="Runtime logs">
    <div className="space-y-4" aria-busy={loading}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground" role="status">{page ? <>{followExecution && <span>Research run </span>}<StatusBadge value={page.job_status} />{` · ${page.attempts} attempt${page.attempts === 1 ? "" : "s"} · ${state.items.length} log entries`}</> : state.error ? "Logs unavailable" : "Loading logs…"}</p>
        <Button variant="outline" size="sm" disabled={!state.items.length} onClick={download}>Download logs</Button>
      </div>
      {state.error && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive"><p>{state.error}</p><Button variant="outline" size="sm" onClick={() => setRefresh(value => value + 1)}>Try again</Button></div>}
      {page && <p className="text-xs text-muted-foreground">Up to {page.max_entries.toLocaleString()} entries per run, kept for {Math.round(page.retention_seconds / 3600)} hours after the last log.</p>}
      {(state.trimmed || state.unreadable > 0) && <p className="text-xs text-muted-foreground">{state.trimmed ? "Older entries were removed by the retention limit. " : ""}{state.unreadable > 0 ? `${state.unreadable} unreadable entries were skipped.` : ""}</p>}
      {!!state.items.length && <>
        <div className="flex flex-wrap items-center gap-3">
          <Input className="w-full sm:max-w-sm" aria-label="Search logs" placeholder="Search logs…" value={query} onChange={event => setQuery(event.target.value)} />
          <Select label="Log level" className="w-full sm:w-52" value={level} onChange={setLevel} required options={[{ value: "all", label: "All levels" }, { value: "problems", label: "Warnings and errors" }, { value: "INFO", label: "Info" }, { value: "DEBUG", label: "Debug" }]} />
        </div>
        <div role="region" aria-label="Runtime log entries" tabIndex={0} className="max-h-[36rem] overflow-auto rounded-md border">
          {shown.length ? <ol>{shown.map(entry => <JobLogLine key={entry.id} entry={entry} />)}</ol> : <p className="p-4 text-sm text-muted-foreground">No entries match these filters.</p>}
        </div>
      </>}
      {page && !state.items.length && !state.error && <p className="text-sm text-muted-foreground">No runtime logs are available. This run may predate log capture, its logs may have expired, or its worker has not started yet. Historical console logs cannot be recovered here.</p>}
    </div>
  </InfoPanel>;
}
