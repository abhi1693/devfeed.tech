"use client";

import Link from "next/link";
import { ArrowDown, ArrowRight, ExternalLink, Focus, GitBranch, Pin, PinOff, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import type { GraphEdge, GraphNode } from "@/lib/api/generated/models";
import { edgeLabel, graphNodeHref } from "@/lib/knowledge-graph";
import { humanize } from "@/lib/resources";
import { StatusBadge } from "./status-badge";

export function GraphInspector({ selected, nodes, edges, pinned, expanded, canExpand, onSelect, onFocus, onExpand, onPin, onClose }: {
  selected: GraphNode | GraphEdge; nodes: GraphNode[]; edges: GraphEdge[]; pinned: string[]; expanded: string[]; canExpand: boolean;
  onSelect: (id: string) => void; onFocus: (id: string) => void; onExpand: (id: string) => void; onPin: (id: string) => void; onClose: () => void;
}) {
  const node = "entity_id" in selected ? selected : undefined;
  const edge = "source" in selected ? selected : undefined;
  const byId = new Map(nodes.map(node => [node.id, node]));
  const connections = node ? edges.filter(edge => edge.source === node.id || edge.target === node.id) : [];
  return <aside aria-label="Graph details" className="min-w-0 shrink-0 overflow-y-auto border-t bg-card p-5 lg:w-80 lg:border-t-0 lg:border-l xl:w-88">
    <div className="mb-4 flex items-start justify-between gap-2"><p className="pt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{node ? humanize(node.kind) : "Connection"}</p><Button variant="ghost" size="icon-xs" aria-label="Close graph details" onClick={onClose}><X /></Button></div>
    {node && <>
      <h2 className="break-words text-lg font-semibold">{node.label}</h2>
      <div className="my-3 flex flex-wrap items-center gap-2 text-xs">{node.status && <StatusBadge value={node.status} />}{node.subtype && <span className="text-muted-foreground">{humanize(node.subtype)}</span>}</div>
      {node.description && <p className="break-words text-sm leading-relaxed text-muted-foreground">{node.description}</p>}
      <div className="mt-5 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onFocus(node.id)}><Focus aria-hidden />Focus</Button>
        <Button size="sm" variant="outline" disabled={!canExpand && !expanded.includes(node.id)} onClick={() => onExpand(node.id)}><GitBranch aria-hidden />{expanded.includes(node.id) ? "Collapse" : "Expand"}</Button>
        <Button size="sm" variant="outline" onClick={() => onPin(node.id)}>{pinned.includes(node.id) ? <PinOff aria-hidden /> : <Pin aria-hidden />}{pinned.includes(node.id) ? "Unpin" : "Pin"}</Button>
        <Button size="sm" variant="ghost" asChild><Link href={graphNodeHref(node)}>Open {node.kind}<ArrowRight aria-hidden /></Link></Button>
      </div>
      {!canExpand && !expanded.includes(node.id) && <p className="mt-2 text-xs text-muted-foreground">Collapse an expansion or focus here to explore further.</p>}
      <div className="mt-6 border-t pt-4"><h3 className="mb-2 text-sm font-medium">Connections in this view{connections.length > 0 && ` (${connections.length})`}</h3>
        {!connections.length && <p className="text-sm text-muted-foreground">No connections in the current view. Try expanding this object or enabling more layers.</p>}
        <ul className="space-y-1">{connections.map(connection => { const target = connection.source === node.id ? connection.target : connection.source; return <li key={connection.id}><button aria-label={`${edgeLabel(connection)} ${byId.get(target)?.label ?? "Object unavailable"}`} onClick={() => onSelect(connection.id)} className="w-full rounded-md p-2 text-left text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"><span className="block text-xs text-muted-foreground">{connection.target === node.id && connection.directed ? "Incoming · " : ""}{edgeLabel(connection)}{connection.status === "pending" && " · Pending"}</span><span className="mt-0.5 block break-words">{byId.get(target)?.label ?? "Object unavailable"}</span></button></li>; })}</ul>
      </div>
    </>}
    {edge && <>
      <h2 className="text-lg font-semibold">{edgeLabel(edge)}</h2>
      <div className="my-3"><StatusBadge value={edge.status} /></div>
      <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-sm">
        <button className="block break-words text-left font-medium hover:underline" onClick={() => onSelect(edge.source)}>{byId.get(edge.source)?.label}</button>
        {edge.directed ? <ArrowDown aria-label="Points to" className="size-4 text-muted-foreground" /> : <span className="text-xs text-muted-foreground">Associated with</span>}
        <button className="block break-words text-left font-medium hover:underline" onClick={() => onSelect(edge.target)}>{byId.get(edge.target)?.label}</button>
      </div>
      {edge.status === "pending" && <p className="mt-4 text-sm text-muted-foreground">AI suggestion awaiting review.</p>}
      <dl className="mt-5 space-y-3 text-sm">{edge.origin && <div><dt className="text-xs text-muted-foreground">Origin</dt><dd>{humanize(edge.origin)}</dd></div>}
        {edge.relevance != null && <div><dt className="text-xs text-muted-foreground">{edge.kind === "recommended" ? "Recommendation score" : edge.kind === "interested_in" ? "Interest strength" : "Article relevance"}</dt><dd>{edge.kind === "recommended" ? edge.relevance.toFixed(1) : `${Math.round(edge.relevance * 100)}%`}</dd></div>}</dl>
      <div className="mt-5 border-t pt-4"><h3 className="mb-2 text-sm font-medium">Evidence</h3>
        {edge.evidence ? <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">{edge.evidence}</p> : <p className="text-sm text-muted-foreground">{edge.evidence_url ? "The saved relationship includes a source link." : "This connection comes from the saved catalog. No supporting excerpt was recorded."}</p>}
        {edge.evidence_url && /^https?:\/\//i.test(edge.evidence_url) && <a className="mt-3 inline-flex items-center gap-1.5 text-sm text-blue-700 hover:underline dark:text-blue-400" href={edge.evidence_url} target="_blank" rel="noopener noreferrer">View evidence<ExternalLink className="size-3.5" aria-hidden /></a>}
      </div>
    </>}
  </aside>;
}
