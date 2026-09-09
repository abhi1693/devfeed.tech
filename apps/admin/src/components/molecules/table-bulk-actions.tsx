"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Dialog } from "radix-ui";
import { X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DeleteConfirmation } from "@/components/molecules/delete-confirmation";
import { ApiError } from "@/lib/api/client";
import { notify, notifyFailure } from "@/lib/notifications";

export type BulkAction<T> = {
  id: string;
  label: string;
  icon?: ReactNode;
  description: string;
  destructive?: boolean;
  eligible?: (row: T) => boolean;
  run: (row: T) => Promise<unknown>;
};

export type BulkActionSource<T> = {
  label: string;
  action: BulkAction<T>;
  loadRows: (signal: AbortSignal) => Promise<T[]>;
};

type Props<T> = {
  label: string;
  selected: T[];
  actions: BulkAction<T>[];
  getRowId: (row: T) => string;
  getRowLabel: (row: T) => string;
  disabled: boolean;
  onClear: () => void;
  onComplete: (ids: string[]) => void;
  onBusyChange: (busy: boolean) => void;
  selectionDescription?: string;
  selectAllControl?: ReactNode;
  sources?: BulkActionSource<T>[];
};

/** A bounded batch of the same guarded operations available on individual records.
 * Freeze the reviewed rows at confirmation; never fetch and approve unseen edits. */
export function TableBulkActions<T>({ label, selected, actions, getRowId, getRowLabel, disabled, onClear, onComplete, onBusyChange, selectionDescription = "on this page", selectAllControl, sources = [] }: Props<T>) {
  const [pending, setPending] = useState<{ action: BulkAction<T>; rows: T[]; skipped: number }>();
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);
  const request = useRef<AbortController | null>(null);
  const [loadingSource, setLoadingSource] = useState<string>();
  useEffect(() => () => request.current?.abort(), []);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<{ succeeded: number; failures: { name: string; message: string }[] }>();

  function close() {
    if (lock.current) return;
    setPending(undefined); setResult(undefined); setConfirmation("");
  }
  async function prepare(source: BulkActionSource<T>, button: HTMLElement) {
    if (request.current || busy || disabled) return;
    trigger.current = button;
    const controller = new AbortController(); request.current = controller;
    setLoadingSource(source.label); onBusyChange(true);
    try {
      const rows = await source.loadRows(controller.signal);
      if (controller.signal.aborted) return;
      const eligible = rows.filter(row => !source.action.eligible || source.action.eligible(row));
      if (!eligible.length) { notify.info("No eligible records match these filters"); return; }
      setPending({ action: source.action, rows: eligible, skipped: rows.length - eligible.length });
      setProgress(0); setResult(undefined); setConfirmation("");
    } catch (error) { if (!controller.signal.aborted) notifyFailure(error, "Could not load matching records"); }
    finally { if (!controller.signal.aborted) { request.current = null; setLoadingSource(undefined); onBusyChange(false); } }
  }
  async function execute() {
    if (!pending || lock.current || result || (pending.action.destructive && confirmation !== "DELETE")) return;
    lock.current = true; setBusy(true); onBusyChange(true);
    const successes: string[] = [];
    const failures: { name: string; message: string }[] = [];
    let index = 0, completed = 0;
    async function worker() {
      while (index < pending!.rows.length) {
        const row = pending!.rows[index++];
        try { await pending!.action.run(row); successes.push(getRowId(row)); }
        catch (error) {
          failures.push({ name: getRowLabel(row), message: error instanceof ApiError ? error.message : "Request failed. Try again." });
          if (error instanceof ApiError && error.status === 401) {
            notifyFailure(error, "Your session expired");
            index = pending!.rows.length;
          }
        }
        setProgress(++completed);
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, pending.rows.length) }, worker));
    setResult({ succeeded: successes.length, failures });
    lock.current = false; setBusy(false); onBusyChange(false);
    // Failed and skipped rows stay selected for inspection or retry.
    onComplete(successes);
    if (!failures.length) {
      notify.success(`${pending.action.label}: ${successes.length} completed`);
      close();
    }
  }

  return <>
    {sources.length > 0 && <div className="flex flex-wrap justify-end gap-2">
      {sources.map(source => <Button key={source.label} variant="outline" size="sm" disabled={disabled || busy || !!loadingSource}
        loading={loadingSource === source.label} loadingText="Loading records…" onClick={event => void prepare(source, event.currentTarget)}>
        {source.action.icon}{source.label}
      </Button>)}
    </div>}
    {selected.length > 0 && <div role="region" aria-label="Selected rows" className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2">
      <span className="mr-2 text-sm font-medium" aria-live="polite">{selected.length} selected {selectionDescription}</span>
      {selectAllControl}
      {actions.map(action => {
        const eligible = selected.filter(row => !action.eligible || action.eligible(row));
        return <Button key={action.id} size="sm" variant={action.destructive ? "destructive-ghost" : "outline"} disabled={disabled || busy || !!loadingSource || !eligible.length}
          title={!eligible.length ? "No selected rows support this action" : undefined}
          onClick={event => { trigger.current = event.currentTarget; setPending({ action, rows: eligible, skipped: selected.length - eligible.length }); setProgress(0); setResult(undefined); setConfirmation(""); }}>
          {action.icon}{action.label}{eligible.length !== selected.length && ` (${eligible.length})`}
        </Button>;
      })}
      <Button size="sm" variant="ghost" className="ml-auto" disabled={busy || !!loadingSource} onClick={onClear}><X aria-hidden />Clear selection</Button>
    </div>}
    <Dialog.Root open={!!pending} onOpenChange={open => { if (!open) close(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-background p-6 shadow-lg"
          onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }}
          onEscapeKeyDown={event => { if (busy) event.preventDefault(); }} onPointerDownOutside={event => { if (busy) event.preventDefault(); }}>
          <Dialog.Title className="text-lg font-semibold">{pending?.action.label} {pending?.rows.length} selected {pending?.rows.length === 1 ? "record" : "records"}?</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">{pending?.action.description}</Dialog.Description>
          {!!pending?.skipped && <p className="mt-2 text-sm">{pending.skipped} ineligible {pending.skipped === 1 ? "row will" : "rows will"} be skipped.</p>}
          <ul aria-label={`${label} to process`} className="my-4 max-h-40 space-y-1 overflow-y-auto rounded-md border p-3 text-sm">
            {pending?.rows.map(row => <li className="break-words" key={getRowId(row)}>{getRowLabel(row)}</li>)}
          </ul>
          {pending?.action.destructive && !result && <DeleteConfirmation value={confirmation} onChange={setConfirmation} disabled={busy} />}
          {busy && <p role="status" className="mt-3 text-sm">Processed {progress} of {pending?.rows.length}…</p>}
          {result && <div role="alert" className="mt-3 space-y-2 text-sm">
            <p>{result.succeeded} completed. {result.failures.length} failed. Review the errors below before retrying.</p>
            <ul className="max-h-40 space-y-2 overflow-y-auto">{result.failures.map((failure, index) => <li key={index}><span className="font-medium">{failure.name}: </span>{failure.message}</li>)}</ul>
          </div>}
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={busy} onClick={close}>{result ? "Done" : "Cancel"}</Button>
            {!result && <Button variant={pending?.action.destructive ? "destructive" : "default"} loading={busy} loadingText="Processing…"
              disabled={disabled || (pending?.action.destructive && confirmation !== "DELETE")} onClick={() => void execute()}>
              {pending?.action.destructive ? "Delete permanently" : `Confirm ${pending?.action.label.toLowerCase()}`}
            </Button>}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>;
}
