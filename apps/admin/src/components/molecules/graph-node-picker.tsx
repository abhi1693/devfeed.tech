"use client";

import { useCallback, useEffect, useState } from "react";
import { adminKnowledgeSearch } from "@/lib/api/generated/admin";
import type { GraphNode } from "@/lib/api/generated/models";
import type { GraphLayer } from "@/lib/knowledge-graph";
import { humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { Combobox } from "./combobox";

export function GraphNodePicker({ label, value, selected, layers, publishedOnly, onChange, className }: {
  label: string; value: string; selected?: GraphNode; layers: GraphLayer[]; publishedOnly?: boolean;
  onChange: (node?: GraphNode) => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [revision, setRevision] = useState(0);
  const [picked, setPicked] = useState<GraphNode>();
  useEffect(() => { const timer = setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  const layerKey = layers.join(",");
  const load = useCallback((signal: AbortSignal) => open ? adminKnowledgeSearch({ q: query, layers: layerKey ? layerKey.split(",") as GraphLayer[] : [], published_only: publishedOnly }, { signal }) : Promise.resolve(null), [open, query, layerKey, publishedOnly]);
  const result = useRequest(`${open}/${query}/${layerKey}/${publishedOnly}/${revision}`, load);
  const option = (node: GraphNode) => ({ value: node.id, label: node.label, description: [humanize(node.kind), node.subtype && humanize(node.subtype)].filter(Boolean).join(" · ") });
  const current = selected?.id === value ? selected : picked?.id === value ? picked : undefined;
  return <Combobox className={className} label={label} value={value} selectedOption={current ? option(current) : value ? { value, label: "Loading object…" } : undefined}
    placeholder={label} clearLabel={label === "To object" ? "None" : "Topic map"} options={(result.data?.items ?? []).map(option)} search={search} onSearchChange={setSearch}
    searchPlaceholder="Search names, titles, aliases…" onOpenChange={setOpen} loading={result.loading || query !== search.trim()}
    error={result.error ? "Could not search the catalog." : undefined} onRetry={() => setRevision(value => value + 1)}
    emptyMessage="No matching objects in these layers."
    footer={result.data && result.data.total > 25 ? <p className="text-xs text-muted-foreground">Showing 25 of {result.data.total.toLocaleString()} matches. Refine your search.</p> : undefined}
    onChange={id => { const node = result.data?.items.find(node => node.id === id); setPicked(node); onChange(node); }} />;
}
