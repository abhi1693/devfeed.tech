import type { JobLogEntry } from "@/lib/api/generated/models";
import { StatusBadge } from "@/components/molecules/status-badge";

/** Log strings are rendered as text, never HTML or terminal escape sequences. */
export function JobLogLine({ entry }: { entry: JobLogEntry }) {
  return <li className="grid min-w-0 gap-2 border-b px-4 py-3 text-sm last:border-b-0 sm:grid-cols-[160px_minmax(0,1fr)]">
    <div className="flex flex-wrap items-center gap-2 self-start text-xs sm:block sm:space-y-1">
      <time dateTime={entry.timestamp} title={entry.timestamp} className="block text-muted-foreground">{new Date(entry.timestamp).toLocaleString()}</time>
      <StatusBadge value={entry.level} label={entry.level} />
      {typeof entry.fields.attempt === "number" && <span className="ml-2 text-muted-foreground">Attempt {entry.fields.attempt}</span>}
    </div>
    <div className="min-w-0 space-y-1">
      <p className="whitespace-pre-wrap break-words">{entry.message}</p>
      <details className="text-xs text-muted-foreground">
        <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-2">Context</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border bg-muted/40 p-3">{JSON.stringify(entry.fields, null, 2)}</pre>
      </details>
    </div>
  </li>;
}
