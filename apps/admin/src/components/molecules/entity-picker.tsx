"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Combobox, type ComboboxOption, type ComboboxProps } from "./combobox";
import { listRecords, getRecord, type RecordData } from "@/lib/resource-api";
import { type Resource, resources, humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";

type Props = Omit<ComboboxProps, "options" | "label"> & { resource: Resource; label?: string; exclude?: string };

export function EntityPicker({ resource, label = resources[resource].singular, exclude, ...props }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [params, setParams] = useState({ q: "", offset: 0 });
  const [revision, setRevision] = useState(0);
  const [picked, setPicked] = useState<ComboboxOption>();
  const query = search.trim();
  useEffect(() => {
    if (query === params.q) return;
    const timer = setTimeout(() => setParams({ q: query, offset: 0 }), 250);
    return () => clearTimeout(timer);
  }, [query, params.q]);
  const load = useCallback((signal: AbortSignal) => open ? listRecords(resource, { ...params, limit: 25 }, signal) : Promise.resolve(null), [resource, params, open]);
  const selected = useCallback((signal: AbortSignal) => props.value ? getRecord(resource, props.value, signal) : Promise.resolve(null), [resource, props.value]);
  const result = useRequest(`${resource}/${open}/${params.q}/${params.offset}/${revision}`, load);
  const current = useRequest(`${resource}/${props.value}`, selected);
  const option = (row: RecordData): ComboboxOption => ({ value: row.id, label: String(row[resources[resource].title]),
    description: [row.slug, row.kind && humanize(String(row.kind)), row.status && humanize(String(row.status))].filter(Boolean).join(" · ") || undefined });
  const choices = (result.data?.items ?? []).filter(row => row.id !== exclude).map(option);
  const total = result.data?.total ?? 0;

  return <Combobox {...props} name={props.name ?? props.id} label={label} options={choices}
    selectedOption={current.data ? option(current.data) : picked}
    onChange={value => { setPicked(choices.find(choice => choice.value === value)); props.onChange(value); }}
    placeholder={`Select ${label.toLowerCase()}…`} searchPlaceholder={`Search ${resources[resource].label.toLowerCase()}…`}
    emptyMessage={`No matching ${resources[resource].label.toLowerCase()}.`} clearLabel="None"
    search={search} onSearchChange={setSearch} onOpenChange={next => { setOpen(next); setSearch(""); setParams({ q: "", offset: 0 }); }}
    loading={query !== params.q || result.loading}
    error={result.error ? "Could not load options. Try again." : undefined} onRetry={() => setRevision(value => value + 1)}
    footer={<div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
      <span role="status">{total.toLocaleString()} matching {total === 1 ? "object" : "objects"}</span>
      {(total > 25 || params.offset > 0) && <div className="flex items-center gap-1">
        <Button type="button" size="icon-xs" variant="ghost" aria-label="Previous options" disabled={!params.offset} onClick={() => setParams(previous => ({ ...previous, offset: Math.max(0, previous.offset - 25) }))}><ChevronLeftIcon /></Button>
        <span>{Math.floor(params.offset / 25) + 1} / {Math.max(1, Math.ceil(total / 25))}</span>
        <Button type="button" size="icon-xs" variant="ghost" aria-label="Next options" disabled={params.offset + 25 >= total} onClick={() => setParams(previous => ({ ...previous, offset: previous.offset + 25 }))}><ChevronRightIcon /></Button>
      </div>}
    </div>} />;
}
