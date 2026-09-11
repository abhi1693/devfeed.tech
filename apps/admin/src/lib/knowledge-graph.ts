import type { Core, ElementDefinition, StylesheetJson, NodeSingular } from "cytoscape";
import type { AdminKnowledgeGraphParams, GraphEdge, GraphNode } from "./api/generated/models";
import { recordHref } from "./routes";
import { humanize } from "./resources";

export type GraphLayer = "article" | "tag" | "source" | "user";
export const graphPath = "/knowledge/graph";
export const nodeKinds = ["topic", "article", "tag", "source", "user"] as const;
export const graphColors = { topic: "#6366d9", article: "#168572", tag: "#b87918", source: "#367abf", user: "#bb5c91" };
export const graphRelations = ["uses_language", "depends_on", "implements", "part_of", "related_to"];
const nodePattern = /^(topic|article|tag|source|user):[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
export function validNodeId(value: string | null) { return value && nodePattern.test(value) ? value : undefined; }
export function graphQuery(search: URLSearchParams): AdminKnowledgeGraphParams {
  const layers = Array.from(new Set(search.getAll("layers").filter((v): v is GraphLayer => ["article", "tag", "source", "user"].includes(v))));
  // Sources connect through articles, so the source layer always includes them.
  if (layers.includes("source") && !layers.includes("article")) layers.push("article");
  return { focus: validNodeId(search.get("focus")), expand: Array.from(new Set(search.getAll("expand").filter(v => validNodeId(v)))).slice(0, 20), layers,
    depth: search.get("depth") === "2" ? 2 : 1, limit: [100, 150, 300].includes(Number(search.get("limit"))) ? Number(search.get("limit")) : 150,
    relation: graphRelations.includes(search.get("relation") ?? "") ? search.get("relation") as AdminKnowledgeGraphParams["relation"] : undefined,
    include_pending: search.get("pending") === "1", published_only: search.get("published") === "1" };
}
export function graphNodeHref(node: GraphNode) {
  if (node.kind === "user") return recordHref("users", { id: node.entity_id });
  const resource = { topic: "topics", article: "articles", tag: "tags", source: "sources" } as const;
  return recordHref(resource[node.kind], { id: node.entity_id });
}
export function openGraphHref(kind: GraphNode["kind"], id: string) {
  const params = new URLSearchParams({ focus: `${kind}:${id}` });
  if (kind !== "topic") params.append("layers", kind);
  if (kind === "source") params.append("layers", "article");
  return `${graphPath}?${params}`;
}
export function edgeLabel(edge: GraphEdge) { return edge.kind === "classified_as" ? `${humanize(edge.role ?? "classified")} topic` : humanize(edge.kind); }

/** Keep small maps legible after Fit, while dense overviews reveal labels on selection. */
export function updateGraphDetail(cy: Core) {
  const zoom = Math.max(.15, cy.zoom());
  cy.nodes().toggleClass("zoomed-out", cy.nodes().length > 80 && zoom < .65)
    .style("font-size", Math.min(28, Math.max(12, 11 / zoom)));
}

/** Pack disconnected groups to the canvas aspect ratio without altering their topology. */
export function packGraphComponents(cy: Core) {
  const components = cy.elements().components();
  if (components.length < 2) return;
  const boxes = components.map(component => ({ component, box: component.boundingBox() }))
    .sort((a, b) => b.box.h - a.box.h);
  const gap = 75;
  const area = boxes.reduce((area, { box }) => area + (box.w + gap) * (box.h + gap), 0);
  const width = Math.max(...boxes.map(({ box }) => box.w), Math.sqrt(area * cy.width() / Math.max(1, cy.height())));
  let x = 0, y = 0, rowHeight = 0;
  cy.batch(() => {
    for (const { component, box } of boxes) {
      if (x && x + box.w > width) { x = 0; y += rowHeight + gap; rowHeight = 0; }
      const dx = x - box.x1, dy = y - box.y1;
      component.nodes().forEach(node => { const position = node.position(); node.position({ x: position.x + dx, y: position.y + dy }); });
      x += box.w + gap; rowHeight = Math.max(rowHeight, box.h);
    }
  });
}

/** Updating evidence must not reset positions, selection, or the viewport. */
export function syncGraph(cy: Core, nodes: GraphNode[], edges: GraphEdge[]) {
  const before = new Set(cy.nodes().map(node => node.id()));
  const ids = new Set([...nodes.map(node => node.id), ...edges.map(edge => edge.id)]);
  cy.batch(() => {
    cy.elements().filter(element => !ids.has(element.id())).remove();
    const additions: ElementDefinition[] = [];
    for (const node of nodes) {
      const data = { ...node, label: node.label.length > 48 ? node.label.slice(0, 45) + "…" : node.label };
      const existing = cy.getElementById(node.id);
      if (existing.length) existing.data(data);
      else additions.push({ group: "nodes", data });
    }
    cy.add(additions);
    for (const edge of edges) {
      const data = { ...edge, label: edgeLabel(edge) };
      const existing = cy.getElementById(edge.id);
      if (existing.length) existing.data(data);
      else cy.add({ group: "edges", data });
    }
  });
  const added = cy.nodes().filter(node => !before.has(node.id()));
  // Place additions near known neighbours before the constrained layout.
  added.forEach((node, index) => {
    const neighbour = node.neighborhood().nodes().filter(other => before.has(other.id())).first();
    const center = neighbour.length ? (neighbour as NodeSingular).position() : { x: 0, y: 0 };
    const angle = index * 2.399963;
    node.position({ x: center.x + Math.cos(angle) * (110 + index % 5 * 20), y: center.y + Math.sin(angle) * (110 + index % 5 * 20) });
  });
  return { before, added };
}

export function graphStyles(dark: boolean): StylesheetJson {
  const text = dark ? "#ededed" : "#262626", background = dark ? "#1c1c1c" : "#ffffff";
  return [
    { selector: "node", style: { width: 34, height: 34, label: "data(label)", color: text, "font-size": 11, "font-family": "Arial, sans-serif", "text-valign": "bottom", "text-margin-y": 9, "text-wrap": "wrap", "text-max-width": "120px", "border-width": 2, "border-opacity": .9, "background-opacity": .2, "text-background-color": background, "text-background-opacity": .85, "text-background-padding": "3px", "overlay-opacity": 0 } },
    ...nodeKinds.map(kind => ({ selector: `node[kind = '${kind}']`, style: { "background-color": graphColors[kind], "border-color": graphColors[kind], shape: ({ topic: "ellipse", article: "round-rectangle", tag: "diamond", source: "hexagon", user: "round-diamond" } as const)[kind] } })),
    { selector: "edge", style: { width: 1.5, "line-color": dark ? "#687586" : "#91a0b5", "target-arrow-color": dark ? "#687586" : "#91a0b5", "target-arrow-shape": "triangle", "arrow-scale": .75, "curve-style": "bezier", "font-size": 10, color: text, "text-rotation": "autorotate", "text-background-color": background, "text-background-opacity": .95, "text-background-padding": "3px", "overlay-opacity": 0 } },
    { selector: "edge[!directed]", style: { "target-arrow-shape": "none" } },
    { selector: "edge[status = 'pending']", style: { "line-style": "dashed", "line-color": "#b87918", "target-arrow-color": "#b87918" } },
    { selector: "node.pinned", style: { "border-width": 4, "border-style": "double" } },
    { selector: ".dim", style: { opacity: .18 } },
    { selector: "node.active, node.highlight", style: { "background-opacity": .65, "border-width": 3, "font-weight": "bold", "z-index": 10 } },
    { selector: "edge.active, edge.highlight", style: { label: "data(label)", width: 2.5, "z-index": 10 } },
    { selector: "node.zoomed-out", style: { label: "" } },
    { selector: "node.zoomed-out.active, node.zoomed-out.highlight", style: { label: "data(label)" } },
  ];
}
