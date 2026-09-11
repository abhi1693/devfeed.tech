"use client";

import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";
import { ArrowRight, FileText, GitBranch, List, LoaderCircle, Maximize, Minus, Network, Plus, Rss, Shapes, SlidersHorizontal, Tags, UserRound, X } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { PageHeading } from "@/components/molecules/page-heading";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { GraphInspector } from "@/components/molecules/graph-inspector";
import { GraphNodePicker } from "@/components/molecules/graph-node-picker";
import { adminKnowledgeGraph, adminKnowledgePath } from "@/lib/api/generated/admin";
import type { GraphNode, GraphOut, GraphPathOut } from "@/lib/api/generated/models";
import { graphColors, graphPath, graphQuery, graphRelations, nodeKinds, validNodeId, type GraphLayer } from "@/lib/knowledge-graph";
import { humanize } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import type { GraphCanvasHandle } from "./graph-canvas";

const GraphCanvas = dynamic(() => import("./graph-canvas"), { ssr: false, loading: () => <div role="status" className="flex min-h-[420px] flex-1 items-center justify-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />Loading graph…</div> });
const icons = { topic: Shapes, article: FileText, tag: Tags, source: Rss, user: UserRound };
const emptyNodes: GraphNode[] = [];
type ExplorerData = GraphOut & { path?: GraphPathOut };

export function KnowledgeGraph() {
  const search = useSearchParams();
  const queryString = search.toString();
  const params = useMemo(() => graphQuery(new URLSearchParams(queryString)), [queryString]);
  const focus = params.focus ?? "";
  const layers = params.layers ?? [];
  const expanded = params.expand ?? [];
  const pathMode = search.get("mode") === "path";
  const target = pathMode ? validNodeId(search.get("to")) ?? "" : "";
  const direction = search.get("direction") === "outgoing" ? "outgoing" : "any";
  const hops = [2, 4, 6].includes(Number(search.get("hops"))) ? Number(search.get("hops")) : 4;
  const [selected, setSelected] = useState<string>();
  const [pinned, setPinned] = useState<string[]>([]);
  const [list, setList] = useState(false);
  const [revision, setRevision] = useState(0);
  const canvas = useRef<GraphCanvasHandle>(null);
  const refreshSeconds = useRefreshInterval();
  const [snapshot, setSnapshot] = useState<{ data: ExplorerData; scope: string }>();
  const scope = JSON.stringify([focus, target]);
  const load = useCallback(async (signal: AbortSignal): Promise<ExplorerData> => {
    let data: ExplorerData;
    if (focus && target) {
      const path = await adminKnowledgePath({ from_node: focus, to_node: target, layers: params.layers, relation: params.relation,
        include_pending: params.include_pending, published_only: params.published_only, direction, max_hops: hops }, { signal });
      data = { ...path, path, catalog_counts: {}, node_limit: 300, edge_limit: 1500 };
    } else data = await adminKnowledgeGraph(params, { signal });
    if (!signal.aborted) setSnapshot({ data, scope });
    return data;
  }, [focus, target, params, direction, hops, scope, setSnapshot]);
  const result = useRequest(`${queryString}/${revision}`, load, refreshSeconds * 1000);
  const display = result.data ?? (result.loading ? snapshot?.data : undefined);
  const displayedScope = result.data ? scope : snapshot?.scope ?? scope;
  const nodes = display?.nodes ?? emptyNodes;
  const edges = display?.edges ?? [];
  const nodeMap = new Map(nodes.map(node => [node.id, node]));
  const current = selected ? nodeMap.get(selected) ?? edges.find(edge => edge.id === selected) : undefined;

  function change(values: Record<string, string | string[] | undefined>) {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(values)) { next.delete(key); if (value) for (const entry of Array.isArray(value) ? value : [value]) next.append(key, entry); }
    // Graph refinements update the bookmark without rerendering the server layout.
    window.history.replaceState(null, "", `${graphPath}${next.size ? `?${next}` : ""}`);
  }
  function focusOn(id?: string) { setSelected(undefined); change({ focus: id, expand: undefined, to: undefined, mode: undefined }); }
  function layer(kind: GraphLayer) {
    let next = layers.includes(kind) ? layers.filter(value => value !== kind) : [...layers, kind];
    if (kind === "source" && next.includes("source") && !next.includes("article")) next.push("article");
    if (kind === "article" && !next.includes("article")) next = next.filter(value => value !== "source");
    const retained = (id: string) => id.startsWith("topic:") || next.some(kind => id.startsWith(`${kind}:`));
    change({ layers: next, focus: retained(focus) ? focus : undefined, to: retained(target) ? target : undefined, expand: undefined });
  }
  function expand(id: string) { change({ expand: expanded.includes(id) ? expanded.filter(value => value !== id) : [...expanded, id].slice(0, 20), mode: undefined, to: undefined }); }
  const pin = (id: string) => setPinned(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id]);
  const pinDragged = (id: string) => setPinned(previous => previous.includes(id) ? previous : [...previous, id]);
  const select = setSelected;
  const path = result.data?.path;
  const filters = !!params.relation || params.include_pending || params.published_only;

  return <section className="min-w-0 space-y-4">
    <PageHeading title="Knowledge graph" description="Explore how topics, articles, tags, and sources connect.">
      <Button variant={pathMode ? "secondary" : "outline"} size="sm" aria-pressed={pathMode} onClick={() => change({ mode: pathMode ? undefined : "path", to: undefined })}><GitBranch aria-hidden />Find a connection</Button>
    </PageHeading>
    <div className="flex flex-wrap items-center gap-2">
      <GraphNodePicker className="w-full sm:w-72 lg:w-80" label={pathMode ? "From object" : "Find an object"} value={focus} selected={nodeMap.get(focus)} layers={layers} publishedOnly={params.published_only} onChange={node => pathMode ? change({ focus: node?.id, expand: undefined }) : focusOn(node?.id)} />
      {pathMode ? <>
        <ArrowRight className="hidden size-4 text-muted-foreground sm:block" aria-hidden />
        <GraphNodePicker className="w-full sm:w-72" label="To object" value={target} selected={nodeMap.get(target)} layers={layers} publishedOnly={params.published_only} onChange={node => change({ to: node?.id })} />
        <Select label="Traversal direction" className="w-44" value={direction} required options={[{ value: "any", label: "Any direction" }, { value: "outgoing", label: "Follow arrows" }]} onChange={value => change({ direction: value })} />
        <Select label="Maximum path length" className="w-36" value={String(hops)} required options={[2, 4, 6].map(value => ({ value: String(value), label: `Up to ${value} links` }))} onChange={value => change({ hops: value })} />
      </> : <Select label="Neighbourhood depth" className="w-44" value={String(params.depth)} required options={[{ value: "1", label: "Direct neighbours" }, { value: "2", label: "Two levels" }]} disabled={!focus} onChange={value => change({ depth: value })} />}
      {focus && <Button variant="ghost" size="sm" onClick={() => focusOn()}>Topic map</Button>}
    </div>
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Graph layers">
          <span className="mr-1 text-xs text-muted-foreground">Layers</span><span className="inline-flex items-center gap-1.5 px-2 text-xs font-medium"><Shapes className="size-3.5" style={{ color: graphColors.topic }} aria-hidden />Topics</span>
          {(["article", "tag", "source", "user"] as const).map(kind => { const Icon = icons[kind]; return <Button key={kind} variant={layers.includes(kind) ? "secondary" : "ghost"} size="sm" aria-pressed={layers.includes(kind)} onClick={() => layer(kind)}><Icon style={{ color: graphColors[kind] }} aria-hidden />{humanize(kind)}s</Button>; })}
        </div>
        <div className="flex items-center gap-2">
          <Popover><PopoverTrigger asChild><Button variant={filters ? "secondary" : "outline"} size="sm"><SlidersHorizontal aria-hidden />Filters{filters && <span className="size-1.5 rounded-full bg-primary" />}</Button></PopoverTrigger>
            <PopoverContent align="end" className="w-72 space-y-4 p-4">
              <div className="space-y-1.5"><p className="text-xs font-medium">Topic relationship</p><Select label="Topic relationship" value={params.relation ?? ""} options={graphRelations.map(value => ({ value, label: humanize(value) }))} clearLabel="All relationships" placeholder="All relationships" onChange={value => change({ relation: value })} /></div>
              {!pathMode && <div className="space-y-1.5"><p className="text-xs font-medium">Visible objects</p><Select label="Visible object limit" value={String(params.limit)} required options={[100, 150, 300].map(value => ({ value: String(value), label: `Up to ${value} objects` }))} onChange={value => change({ limit: value })} /></div>}
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-0.5 accent-primary" checked={params.include_pending} onChange={event => change({ pending: event.target.checked ? "1" : undefined })} /><span>Show pending suggestions<span className="mt-1 block text-xs text-muted-foreground">Dashed connections awaiting review.</span></span></label>
              {layers.includes("article") && <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-primary" checked={params.published_only} onChange={event => change({ published: event.target.checked ? "1" : undefined, focus: focus.startsWith("article:") ? undefined : focus, to: target.startsWith("article:") ? undefined : target, expand: undefined })} />Published articles only</label>}
              {filters && <Button variant="ghost" size="sm" onClick={() => change({ relation: undefined, pending: undefined, published: undefined })}>Clear filters</Button>}
            </PopoverContent></Popover>
          <Button size="sm" variant={list ? "secondary" : "outline"} aria-pressed={list} onClick={() => setList(value => !value)}>{list ? <Network aria-hidden /> : <List aria-hidden />}{list ? "Graph view" : "Connection list"}</Button>
        </div>
      </div>
      {expanded.length > 0 && <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs"><span className="text-muted-foreground">Expanded</span>{expanded.map(id => <Button key={id} variant="secondary" size="sm" className="h-6 max-w-52 px-2 text-xs" onClick={() => expand(id)} aria-label={`Collapse ${nodeMap.get(id)?.label ?? "object"}`}><span className="truncate">{nodeMap.get(id)?.label ?? "Loading…"}</span><X aria-hidden /></Button>)}</div>}
      {(result.error || result.loading) && <div className="px-4"><RequestState loading={result.loading} error={result.error} retry={() => setRevision(value => value + 1)} /></div>}
      {pathMode && (!focus || !target) && <p role="status" className="border-b bg-muted/30 px-4 py-3 text-sm text-muted-foreground">Choose two objects to find a connection through the selected layers.</p>}
      {path && <p role="status" className="border-b bg-muted/30 px-4 py-3 text-sm">{path.found ? path.edges.length ? `Connected through ${path.edges.length} ${path.edges.length === 1 ? "link" : "links"}${path.edges.some(edge => edge.status === "pending") ? ", including pending suggestions" : ""}.` : "Both selections are the same object." : path.truncated ? "Search limit reached. A connection may exist; narrow the layers or choose a closer starting object." : `No connection found within ${hops} links in these layers${direction === "outgoing" ? " following the arrows" : ""}.`}</p>}
      {result.data && !nodes.length && <div className="flex min-h-96 flex-col items-center justify-center gap-3 p-6 text-center"><Network className="size-10 text-muted-foreground/60" aria-hidden /><h2 className="text-lg font-medium">{filters ? "No connections match these filters" : "Your knowledge graph starts with active topics"}</h2><p className="max-w-md text-sm text-muted-foreground">{filters ? "Adjust the filters or enable another layer to explore more of the catalog." : "As topics are approved and articles are classified, their connections will appear here."}</p></div>}
      {nodes.length > 0 && <div inert={result.loading} aria-busy={result.loading} className="flex min-h-[480px] flex-col lg:h-[calc(100dvh-390px)] lg:min-h-[440px] lg:flex-row">
        <div className="relative flex min-h-[420px] min-w-0 flex-1 flex-col bg-[radial-gradient(var(--border)_1px,transparent_1px)] bg-size-[20px_20px]">
          {/* Keep the canvas mounted behind the list so positions survive view switches. */}
          <div className={list ? "invisible absolute inset-0" : "flex min-h-[420px] flex-1"}><GraphCanvas ref={canvas} nodes={nodes} edges={edges} scope={displayedScope} selected={selected} pinned={pinned} onSelect={select} onPin={pinDragged} /></div>
          {list && <div className="min-h-[420px] flex-1 overflow-y-auto bg-card p-3"><p className="px-2 pb-3 text-xs text-muted-foreground">Select an object to inspect its connections.</p><ul aria-label="Graph objects" className="grid content-start gap-1 sm:grid-cols-2 xl:grid-cols-3">{nodes.map(node => { const Icon = icons[node.kind]; return <li key={node.id}><button aria-label={`${node.label} · ${humanize(node.kind)}`} aria-pressed={selected === node.id} className={`flex w-full items-center gap-3 rounded-md p-3 text-left text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring ${selected === node.id ? "bg-accent" : ""}`} onClick={() => setSelected(node.id)}><Icon className="size-5 shrink-0" style={{ color: graphColors[node.kind] }} aria-hidden /><span className="min-w-0"><span className="block truncate">{node.label}</span><span className="text-xs text-muted-foreground">{humanize(node.kind)}</span></span></button></li>; })}</ul></div>}
          {!list && <div className="absolute right-3 bottom-3 flex rounded-md border bg-card p-1 shadow-sm" role="group" aria-label="Graph controls">
            <Button size="icon-sm" variant="ghost" title="Zoom in" aria-label="Zoom in" onClick={() => canvas.current?.zoom(1.25)}><Plus /></Button>
            <Button size="icon-sm" variant="ghost" title="Zoom out" aria-label="Zoom out" onClick={() => canvas.current?.zoom(.8)}><Minus /></Button>
            <Button size="sm" variant="ghost" title="Show all loaded objects" aria-label="Fit graph" onClick={() => canvas.current?.fit()}><Maximize aria-hidden />Fit view</Button>
            <Button size="icon-sm" variant="ghost" title="Arrange graph" aria-label="Arrange graph" onClick={() => canvas.current?.arrange()}><Network /></Button>
          </div>}
        </div>
        {current && <GraphInspector selected={current} nodes={nodes} edges={edges} pinned={pinned} expanded={expanded} canExpand={expanded.length < 20} onSelect={select} onFocus={focusOn} onExpand={expand} onPin={pin} onClose={() => setSelected(undefined)} />}
      </div>}
      {result.data && nodes.length > 0 && <div className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-3 text-xs text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-label="Graph legend">{nodeKinds.filter(kind => nodes.some(node => node.kind === kind)).map(kind => { const Icon = icons[kind]; return <span key={kind} className="inline-flex items-center gap-1.5"><Icon className="size-3.5" style={{ color: graphColors[kind] }} aria-hidden />{nodes.filter(node => node.kind === kind).length.toLocaleString()} {kind}{nodes.filter(node => node.kind === kind).length !== 1 ? "s" : ""}</span>; })}<span>{edges.length.toLocaleString()} connections</span>{params.include_pending && <span className="inline-flex items-center gap-1.5"><span className="w-5 border-t-2 border-dashed border-amber-600" aria-hidden />Pending</span>}{result.refreshing && <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" aria-label="Updating graph" />}</div>
        <span>{!path && result.data.truncated ? `View limited to ${result.data.node_limit} objects / ${result.data.edge_limit} connections. Focus an object to explore more.` : "Select to inspect · Drag to pin"}</span>
      </div>}
    </div>
    {!path && result.data && Object.keys(result.data.catalog_counts).length > 0 && <p className="text-xs text-muted-foreground">Catalog in selected layers: {nodeKinds.filter(kind => result.data!.catalog_counts[kind]).map(kind => `${result.data!.catalog_counts[kind].toLocaleString()} ${kind}${result.data!.catalog_counts[kind] === 1 ? "" : "s"}`).join(" · ")}. Topic map includes active topics.</p>}
  </section>;
}
