"use client";

import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import cytoscape, {
  type Core,
  type LayoutOptions,
  type NodeSingular,
  type EdgeSingular,
} from "cytoscape";
import fcose from "cytoscape-fcose";
import { LoaderCircle } from "lucide-react";
import type { GraphEdge, GraphNode } from "@/lib/api/generated/models";
import {
  graphStyles,
  syncGraph,
  packGraphComponents,
  updateGraphDetail,
} from "@/lib/knowledge-graph";

cytoscape.use(fcose);
export type GraphCanvasHandle = {
  fit: () => void;
  zoom: (factor: number) => void;
  arrange: () => void;
};
type Props = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  scope: string;
  selected?: string;
  pinned: string[];
  onSelect: (id?: string) => void;
  onPin: (id: string) => void;
  ref?: Ref<GraphCanvasHandle>;
};

export default function GraphCanvas({
  nodes,
  edges,
  scope,
  selected,
  pinned,
  onSelect,
  onPin,
  ref,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const core = useRef<Core | null>(null);
  const previousScope = useRef(scope);
  const callbacks = useRef({ onSelect, onPin });
  const frame = useRef(0);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    callbacks.current = { onSelect, onPin };
  }, [onSelect, onPin]);

  const arrange = useCallback(
    (fixed: Set<string> = new Set(), fit = true) => {
      const cy = core.current;
      if (!cy || !cy.nodes().length) return;
      cancelAnimationFrame(frame.current);
      setBusy(true);
      frame.current = requestAnimationFrame(() => {
        if (cy.destroyed()) return;
        try {
          cy.layout({
            name: "fcose",
            quality: "default",
            animate: false,
            fit,
            padding: 55,
            nodeDimensionsIncludeLabels: true,
            nodeSeparation: 100,
            idealEdgeLength: 130,
            fixedNodeConstraint: cy
              .nodes()
              .filter((node) => fixed.has(node.id()) || node.locked())
              .map((node) => ({ nodeId: node.id(), position: (node as NodeSingular).position() })),
          } as LayoutOptions).run();
          if (fit && !fixed.size && !cy.nodes(":locked").length) {
            packGraphComponents(cy);
            cy.fit(undefined, 55);
          }
          updateGraphDetail(cy);
        } catch {
          // A disconnected or constrained graph still gets a usable layout.
          cy.nodes()
            .filter((node) => !fixed.has(node.id()) && !node.locked())
            .layout({ name: "grid", fit: false, avoidOverlap: true, spacingFactor: 1.5 })
            .run();
          if (fit) cy.fit(undefined, 55);
        } finally {
          setBusy(false);
        }
      });
    },
    [setBusy],
  );

  useImperativeHandle(ref, () => ({
    fit: () => core.current?.fit(undefined, 55),
    zoom: (factor) => {
      const cy = core.current;
      if (cy)
        cy.zoom({
          level: cy.zoom() * factor,
          renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
        });
    },
    arrange: () => arrange(),
  }));

  useEffect(() => {
    if (!container.current) return;
    let cy: Core;
    try {
      cy = cytoscape({
        container: container.current,
        elements: [],
        style: graphStyles(document.documentElement.classList.contains("dark")),
        minZoom: 0.15,
        maxZoom: 3,
        boxSelectionEnabled: false,
        selectionType: "single",
      });
    } catch {
      let cancelled = false;
      queueMicrotask(() => {
        if (!cancelled) setFailed(true);
      });
      return () => {
        cancelled = true;
      };
    }
    core.current = cy;
    cy.on("tap", "node, edge", (event) => callbacks.current.onSelect(event.target.id()));
    cy.on("tap", (event) => {
      if (event.target === cy) callbacks.current.onSelect(undefined);
    });
    cy.on("dragfree", "node", (event) => callbacks.current.onPin(event.target.id()));
    cy.on("zoom", () => updateGraphDetail(cy));
    cy.on("mouseover", "node, edge", (event) => event.target.addClass("highlight"));
    cy.on("mouseout", "node, edge", (event) => event.target.removeClass("highlight"));
    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(container.current);
    const theme = new MutationObserver(() =>
      cy.style(graphStyles(document.documentElement.classList.contains("dark"))),
    );
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => {
      cancelAnimationFrame(frame.current);
      observer.disconnect();
      theme.disconnect();
      cy.destroy();
      core.current = null;
    };
  }, []);

  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    if (scope !== previousScope.current) {
      cy.elements().remove();
      previousScope.current = scope;
    }
    const { before, added } = syncGraph(cy, nodes, edges);
    if (added.length) arrange(before, !before.size);
    updateGraphDetail(cy);
    // Polls with the same objects only update their data, never the layout.
  }, [nodes, edges, scope, arrange]);

  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("dim active highlight");
      if (selected) {
        const element = cy.getElementById(selected);
        if (element.length) {
          const neighbours = element.isNode()
            ? element.closedNeighborhood()
            : (element as unknown as EdgeSingular).connectedNodes().union(element);
          cy.elements().difference(neighbours).addClass("dim");
          neighbours.addClass("highlight");
          element.addClass("active");
        }
      }
      cy.nodes().forEach((node) => {
        const fixed = pinned.includes(node.id());
        node.toggleClass("pinned", fixed);
        if (fixed) node.lock();
        else node.unlock();
      });
    });
  }, [selected, pinned, nodes, edges]);

  if (failed)
    return (
      <p role="alert" className="p-6 text-sm">
        The graph could not render in this browser. Use the connection list to explore the same
        data.
      </p>
    );
  return (
    <div className="relative h-full min-h-[420px] flex-1">
      <div
        ref={container}
        style={{ position: "absolute", inset: 0 }}
        className="absolute inset-0 outline-offset-[-3px]"
        tabIndex={0}
        role="img"
        aria-label="Knowledge graph. Arrow keys pan; plus and minus zoom. Use the connection list to select objects with the keyboard."
        onKeyDown={(event) => {
          const cy = core.current;
          if (!cy) return;
          const move: Record<string, { x: number; y: number }> = {
            ArrowLeft: { x: 50, y: 0 },
            ArrowRight: { x: -50, y: 0 },
            ArrowUp: { x: 0, y: 50 },
            ArrowDown: { x: 0, y: -50 },
          };
          if (move[event.key]) {
            event.preventDefault();
            cy.panBy(move[event.key]);
          }
          if (["+", "=", "-"].includes(event.key)) {
            event.preventDefault();
            cy.zoom(cy.zoom() * (event.key === "-" ? 0.8 : 1.2));
          }
          if (event.key === "Escape") onSelect(undefined);
        }}
      />
      {busy && (
        <div
          role="status"
          className="pointer-events-none absolute top-3 left-3 flex items-center gap-2 rounded-md border bg-card/95 px-3 py-2 text-xs shadow-sm"
        >
          <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
          Arranging connections…
        </div>
      )}
    </div>
  );
}
