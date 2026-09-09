"use client";

import { useCallback, useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { SearchField } from "@/components/molecules/search-field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { Select } from "@/components/molecules/select";
import { Combobox } from "@/components/molecules/combobox";
import { Field } from "@/components/molecules/field";
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
  scopeKey?: string;
  revision: number;
  onChange: (values: Record<string, string>, replace?: boolean) => void;
  onRefresh: () => void;
};

export function TopicProposalFilters({ filters, q, batchId, scopeKey, revision, onChange, onRefresh }: Props) {
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
    <SearchField className="basis-full sm:basis-auto sm:flex-1 sm:max-w-sm" label="Search proposals" placeholder="Search topics, keywords, sources…" value={q} scopeKey={scopeKey ?? JSON.stringify([filters, batchId])} onSearch={q => onChange({ q, offset: "0" }, true)} />
    <Select label="AI analysis filter" className="w-44" value={filters.analysis ?? ""} options={analysisChoices}
      placeholder="All AI statuses" clearLabel="All AI statuses" onChange={value => select("analysis", value)} />
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild><Button variant="outline" size="sm" aria-label={extraCount ? `Filters (${extraCount} active)` : "Filters"}>
        <SlidersHorizontal aria-hidden />Filters{extraCount > 0 && <span className="rounded bg-muted px-1.5 text-xs tabular-nums">{extraCount}</span>}
      </Button></PopoverTrigger>
      <PopoverContent aria-label="Proposal filters" className="w-80 max-w-[calc(100vw-1.5rem)] space-y-4 p-4">
        <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Filter proposals</h2><Button variant="ghost" size="icon-sm" aria-label="Close filters" onClick={() => setOpen(false)}><X aria-hidden /></Button></div>
        <Field label="Source">{control => <Combobox {...control} label="Source" value={filters.source ?? ""} options={(options.data?.sources ?? []).map(value => ({ value, label: value }))}
          placeholder="All sources" clearLabel="All sources" loading={options.loading} error={options.error?.message} onRetry={onRefresh} onChange={value => select("source", value)} />}</Field>
        <Field label="Kind">{control => <Combobox {...control} label="Kind" value={filters.kind ?? ""} options={(options.data?.kinds ?? []).map(value => ({ value, label: humanize(value) }))}
          placeholder="All kinds" clearLabel="All kinds" loading={options.loading} error={options.error?.message} onRetry={onRefresh} onChange={value => select("kind", value)} />}</Field>
        <Field label="Change">{control => <Select {...control} label="Change" value={filters.action ?? ""} options={actionChoices} placeholder="All changes" clearLabel="All changes" onChange={value => select("action", value)} />}</Field>
        <Field label="Missing information">{control => <Select {...control} label="Missing information" value={filters.missing ?? ""} options={missingChoices} placeholder="All proposals" clearLabel="All proposals" onChange={value => select("missing", value)} />}</Field>
      </PopoverContent>
    </Popover>
    {chips.length > 0 && <div aria-label="Active proposal filters" className="order-last flex w-full flex-wrap items-center gap-2">
      {chips.map(chip => <Button key={chip.key} variant="outline" size="xs" className="max-w-full rounded-full bg-muted/40 font-normal" aria-label={`Remove ${chip.label.toLowerCase()} filter`} title={`${chip.label}: ${chip.value}`}
        onClick={() => onChange({ [chip.key]: "", offset: "0" })}><span className="truncate"><span className="text-muted-foreground">{chip.label}:</span> {chip.value}</span><X aria-hidden /></Button>)}
      <Button variant="ghost" size="xs" onClick={() => onChange(clearedProposalFilters)}>Clear filters</Button>
    </div>}
  </>;
}
