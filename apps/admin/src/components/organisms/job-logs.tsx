"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Combobox } from "@/components/molecules/combobox";
import { InfoPanel } from "@/components/molecules/info-panel";
import { JobLogLine } from "@/components/molecules/job-log-entry";
import { adminJobLogs } from "@/lib/api/generated/admin";
import type { AdminJobLogs, JobLogEntry } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { StatusBadge } from "@/components/molecules/status-badge";
import { notifyFailure } from "@/lib/notifications";

type Props = { kind: Parameters<typeof adminJobLogs>[0]; id: string };
type LogState = { page?: AdminJobLogs; items: JobLogEntry[]; error?: string; unreadable: number; trimmed: boolean };

export function JobLogs(props: Props) {
  // Navigating directly between runs must discard the previous run's cursor/data.
  return <LogViewer key={`${props.kind}/${props.id}`} {...props} />;
}

function LogViewer({ kind, id }: Props) {
  const [state, setState] = useState<LogState>({ items: [], unreadable: 0, trimmed: false });
  const [live, setLive] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");
  const failed = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor: string | undefined;
    let items: JobLogEntry[] = [];
    let terminalPolls = 0, unreadable = 0, trimmed = false;
    async function poll() {
      try {
        const page = await adminJobLogs(kind, id, { after: cursor, limit: 200 }, { signal: abort.signal });
        if (abort.signal.aborted) return;
        failed.current = false;
        const merged = new Map(items.map(entry => [entry.id, entry]));
        page.items.forEach(entry => merged.set(entry.id, entry));
        trimmed = trimmed || page.truncated || merged.size > page.max_entries;
        items = Array.from(merged.values()).slice(-page.max_entries);
        unreadable += page.unreadable_entries;
        cursor = page.next_cursor ?? cursor;
        setState({ page, items, unreadable, trimmed });
        terminalPolls = !page.has_more && ["succeeded", "failed"].includes(page.job_status) ? terminalPolls + 1 : 0;
        // A final log may follow the DB status commit. Drain two settling polls.
        if (page.has_more || (live && terminalPolls < 3)) {
          timer = setTimeout(poll, page.has_more ? 0 : 3000);
        }
      } catch (error) {
        if (abort.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) { returnToLogin(); return; }
        if (!failed.current) notifyFailure(error, "Could not load runtime logs", `job-logs-${kind}-${id}`);
        failed.current = true;
        setState(previous => ({ ...previous, error: error instanceof ApiError && error.status === 404 ? "This run no longer exists." : "Could not load runtime logs. Retry or check the worker console." }));
        if (live && !(error instanceof ApiError && [403, 404].includes(error.status))) timer = setTimeout(poll, 10000);
      }
    }
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [kind, id, refresh, live]);

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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground" role="status">{page ? <><StatusBadge value={page.job_status} />{` · ${page.attempts} attempt${page.attempts === 1 ? "" : "s"} · ${state.items.length} log entries`}</> : state.error ? "Logs unavailable" : "Loading logs…"}</p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs"><Input type="checkbox" checked={live} onChange={event => setLive(event.target.checked)} />Live updates</label>
          <Button variant="outline" size="sm" onClick={() => setRefresh(value => value + 1)}>Refresh logs</Button>
          <Button variant="outline" size="sm" disabled={!state.items.length} onClick={download}>Download logs</Button>
        </div>
      </div>
      {state.error && <p role="alert" className="text-sm text-destructive">{state.error}</p>}
      {page && <p className="text-xs text-muted-foreground">Up to {page.max_entries.toLocaleString()} entries per run, kept for {Math.round(page.retention_seconds / 3600)} hours after the last log. Live updates stop when the run finishes.</p>}
      {(state.trimmed || state.unreadable > 0) && <p className="text-xs text-muted-foreground">{state.trimmed ? "Older entries were removed by the retention limit. " : ""}{state.unreadable > 0 ? `${state.unreadable} unreadable entries were skipped.` : ""}</p>}
      {!!state.items.length && <>
        <div className="flex flex-wrap items-center gap-3">
          <Input className="w-full sm:max-w-sm" aria-label="Search logs" placeholder="Search logs…" value={query} onChange={event => setQuery(event.target.value)} />
          <Combobox label="Log level" className="w-full sm:w-52" value={level} onChange={setLevel} required options={[{ value: "all", label: "All levels" }, { value: "problems", label: "Warnings and errors" }, { value: "INFO", label: "Info" }, { value: "DEBUG", label: "Debug" }]} />
        </div>
        <div role="region" aria-label="Runtime log entries" tabIndex={0} className="max-h-[36rem] overflow-auto rounded-md border">
          {shown.length ? <ol>{shown.map(entry => <JobLogLine key={entry.id} entry={entry} />)}</ol> : <p className="p-4 text-sm text-muted-foreground">No entries match these filters.</p>}
        </div>
      </>}
      {page && !state.items.length && !state.error && <p className="text-sm text-muted-foreground">No runtime logs are available. This run may predate log capture, its logs may have expired, or its worker has not started yet. Historical console logs cannot be recovered here.</p>}
    </div>
  </InfoPanel>;
}
