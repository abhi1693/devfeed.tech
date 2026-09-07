"use client";

import { useCallback, useId, useState } from "react";
import { RotateCw, Search, SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { Combobox } from "@/components/molecules/combobox";
import { adminTopicProposalFilterOptions } from "@/lib/api/generated/admin";
import type { AdminTopicProposalsListParams } from "@/lib/api/generated/models";
import { humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";

type FilterKey = "kind" | "source" | "action" | "analysis" | "missing";
type Filters = Pick<AdminTopicProposalsListParams, FilterKey>;
type Choice<K extends FilterKey> = { value: NonNullable<Filters[K]>; label: string };
const analysisChoices: Choice<"analysis">[] = [
  { value: "not_run", label: "Not run" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "enriched", label: "Ready for review" },
  { value: "no_additions", label: "No additions" },
  { value: "failed", label: "Failed" },
];
const actionChoices: Choice<"action">[] = [{ value: "create", label: "New topic" }, { value: "update", label: "Update topic" }];
const missingChoices: Choice<"missing">[] = [
  { value: "any", label: "Any field" },
  { value: "description", label: "Description" },
  { value: "aliases", label: "Aliases" },
  { value: "keywords", label: "Keywords" },
  { value: "website_url", label: "Website" },
  { value: "logo_url", label: "Logo" },
  { value: "facts", label: "Sourced facts" },
];

export function readProposalFilters(search: Pick<URLSearchParams, "get">): Filters {
  return {
    kind: search.get("kind")?.slice(0, 100) || undefined,
    source: search.get("source")?.slice(0, 200) || undefined,
    action: actionChoices.find(choice => choice.value === search.get("action"))?.value,
    analysis: analysisChoices.find(choice => choice.value === search.get("analysis"))?.value,
    missing: missingChoices.find(choice => choice.value === search.get("missing"))?.value,
  };
}

export const clearedProposalFilters = { q: "", batch_id: "", kind: "", source: "", action: "", analysis: "", missing: "", offset: "0" };

type Props = {
  filters: Filters;
  q: string;
  batchId?: string;
  revision: number;
  loading: boolean;
  onChange: (values: Record<string, string>) => void;
  onRefresh: () => void;
};

export function TopicProposalFilters({ filters, q, batchId, revision, loading, onChange, onRefresh }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const load = useCallback((signal: AbortSignal) => adminTopicProposalFilterOptions({ signal }), []);
  const options = useRequest(`proposal-filter-options/${revision}`, load);
  const extraCount = [filters.kind, filters.source, filters.action, filters.missing].filter(Boolean).length;
  const chips = [
    { key: "q", label: "Search", value: q },
    { key: "analysis", label: "AI", value: analysisChoices.find(choice => choice.value === filters.analysis)?.label },
    { key: "source", label: "Source", value: filters.source },
    { key: "kind", label: "Kind", value: filters.kind && humanize(filters.kind) },
    { key: "action", label: "Change", value: actionChoices.find(choice => choice.value === filters.action)?.label },
    { key: "missing", label: "Missing", value: missingChoices.find(choice => choice.value === filters.missing)?.label },
    { key: "batch_id", label: "Batch", value: batchId && "Current import" },
  ].filter(chip => chip.value);
  function select(key: FilterKey, value: string) { onChange({ [key]: value, offset: "0" }); }

  return <>
    <form key={q} role="search" className="flex min-w-0 basis-full gap-2 sm:basis-auto sm:flex-1 sm:max-w-sm" onSubmit={event => {
      event.preventDefault();
      onChange({ q: String(new FormData(event.currentTarget).get("q") || "").trim(), offset: "0" });
    }}>
      <div className="relative min-w-0 flex-1"><Search aria-hidden className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" />
        <Input aria-label="Search proposals" name="q" defaultValue={q} maxLength={200} placeholder="Search topics, keywords, sources…" className="pl-9" />
      </div>
      <Button variant="outline" type="submit">Search</Button>
    </form>
    <Combobox label="AI analysis filter" className="w-44" value={filters.analysis ?? ""} options={analysisChoices}
      placeholder="All AI statuses" clearLabel="All AI statuses" onChange={value => select("analysis", value)} />
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild><Button variant="outline" size="sm" aria-label={extraCount ? `Filters (${extraCount} active)` : "Filters"}>
        <SlidersHorizontal aria-hidden />Filters{extraCount > 0 && <span className="rounded bg-muted px-1.5 text-xs tabular-nums">{extraCount}</span>}
      </Button></PopoverTrigger>
      <PopoverContent aria-label="Proposal filters" className="w-80 max-w-[calc(100vw-1.5rem)] space-y-4 p-4">
        <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Filter proposals</h2><Button variant="ghost" size="icon-sm" aria-label="Close filters" onClick={() => setOpen(false)}><X aria-hidden /></Button></div>
        <div className="space-y-1.5"><label htmlFor={`${id}-source`} className="text-sm font-medium">Source</label>
          <Combobox id={`${id}-source`} label="Source" value={filters.source ?? ""} options={(options.data?.sources ?? []).map(value => ({ value, label: value }))}
            placeholder="All sources" clearLabel="All sources" loading={options.loading} error={options.error?.message} onRetry={onRefresh} onChange={value => select("source", value)} />
        </div>
        <div className="space-y-1.5"><label htmlFor={`${id}-kind`} className="text-sm font-medium">Kind</label>
          <Combobox id={`${id}-kind`} label="Kind" value={filters.kind ?? ""} options={(options.data?.kinds ?? []).map(value => ({ value, label: humanize(value) }))}
            placeholder="All kinds" clearLabel="All kinds" loading={options.loading} error={options.error?.message} onRetry={onRefresh} onChange={value => select("kind", value)} />
        </div>
        <div className="space-y-1.5"><label htmlFor={`${id}-action`} className="text-sm font-medium">Change</label>
          <Combobox id={`${id}-action`} label="Change" value={filters.action ?? ""} options={actionChoices} placeholder="All changes" clearLabel="All changes" onChange={value => select("action", value)} />
        </div>
        <div className="space-y-1.5"><label htmlFor={`${id}-missing`} className="text-sm font-medium">Missing information</label>
          <Combobox id={`${id}-missing`} label="Missing information" value={filters.missing ?? ""} options={missingChoices} placeholder="All proposals" clearLabel="All proposals" onChange={value => select("missing", value)} />
        </div>
      </PopoverContent>
    </Popover>
    <Button variant="ghost" size="icon-sm" aria-label="Refresh" title="Refresh proposals" onClick={onRefresh} loading={loading}><RotateCw aria-hidden /></Button>
    {chips.length > 0 && <div aria-label="Active proposal filters" className="order-last flex w-full flex-wrap items-center gap-2">
      {chips.map(chip => <Button key={chip.key} variant="outline" size="xs" className="max-w-full rounded-full bg-muted/40 font-normal" aria-label={`Remove ${chip.label.toLowerCase()} filter`} title={`${chip.label}: ${chip.value}`}
        onClick={() => onChange({ [chip.key]: "", offset: "0" })}><span className="truncate"><span className="text-muted-foreground">{chip.label}:</span> {chip.value}</span><X aria-hidden /></Button>)}
      <Button variant="ghost" size="xs" onClick={() => onChange(clearedProposalFilters)}>Clear filters</Button>
    </div>}
  </>;
}
