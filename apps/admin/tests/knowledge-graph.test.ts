import { describe, expect, it } from "vitest";
import cytoscape, { type NodeSingular } from "cytoscape";
import { edgeLabel, graphQuery, openGraphHref, syncGraph } from "@/lib/knowledge-graph";
import type { GraphEdge, GraphNode } from "@/lib/api/generated/models";

const first: GraphNode = {
  id: "topic:a",
  entity_id: "a",
  label: "React",
  kind: "topic",
  description: null,
  status: "active",
  subtype: "technology",
};
const second = { ...first, id: "topic:b", entity_id: "b", label: "JavaScript" };
const edge: GraphEdge = {
  id: "a/uses/b",
  source: first.id,
  target: second.id,
  kind: "uses_language",
  directed: true,
  status: "approved",
  role: null,
  origin: null,
  evidence: "A saved excerpt",
  evidence_url: null,
  relevance: null,
};

describe("graph updates", () => {
  it("retains camera, dragged positions, and locks when polling changes metadata", () => {
    const cy = cytoscape({ headless: true });
    syncGraph(cy, [first, second], [edge]);
    const node = cy.getElementById(first.id) as NodeSingular;
    node.position({ x: 312, y: 147 });
    node.lock();
    cy.zoom(1.4);
    cy.pan({ x: 20, y: 80 });
    const result = syncGraph(
      cy,
      [{ ...first, label: "Updated React" }, second],
      [{ ...edge, evidence: "Updated evidence" }],
    );
    expect(result.added.length).toBe(0);
    expect(node.position()).toEqual({ x: 312, y: 147 });
    expect(node.locked()).toBe(true);
    expect(cy.zoom()).toBe(1.4);
    expect(cy.pan()).toEqual({ x: 20, y: 80 });
    expect(cy.getElementById(edge.id).data("evidence")).toBe("Updated evidence");
    cy.destroy();
  });
  it("adds neighbours without moving existing nodes and removes stale connections", () => {
    const cy = cytoscape({ headless: true });
    syncGraph(cy, [first], []);
    const node = cy.getElementById(first.id) as NodeSingular;
    node.position({ x: 100, y: 80 });
    expect(syncGraph(cy, [first, second], [edge]).added.length).toBe(1);
    expect(node.position()).toEqual({ x: 100, y: 80 });
    syncGraph(cy, [first], []);
    expect(cy.nodes().length).toBe(1);
    expect(cy.edges().length).toBe(0);
    cy.destroy();
  });
});

it("bounds bookmark parameters and includes the article bridge for sources", () => {
  const query = graphQuery(
    new URLSearchParams(
      "layers=source&layers=source&layers=invalid&limit=99999&depth=99&focus=invalid&relation=unknown",
    ),
  );
  expect(query.layers).toEqual(["source", "article"]);
  expect(query.limit).toBe(150);
  expect(query.depth).toBe(1);
  expect(query.focus).toBeUndefined();
  expect(query.relation).toBeUndefined();
});

it("opens the right layers from record pages and names classification roles", () => {
  expect(openGraphHref("source", "abc")).toContain("layers=article");
  expect(openGraphHref("tag", "abc")).toContain("layers=tag");
  expect(edgeLabel({ ...edge, kind: "classified_as", role: "comparison" })).toBe(
    "Comparison topic",
  );
});
