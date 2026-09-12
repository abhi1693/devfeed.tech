// @vitest-environment jsdom
import { useEffect } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { KnowledgeGraph } from "@/components/organisms/knowledge-graph";
import * as api from "@/lib/api/generated/admin";
import type { GraphNode, GraphOut } from "@/lib/api/generated/models";

const state = vi.hoisted(() => ({ query: "", replace: vi.fn(), mounts: 0 }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: state.replace }),
  useSearchParams: () => new URLSearchParams(state.query),
}));
vi.mock("next/dynamic", () => ({
  default: () =>
    function Canvas() {
      useEffect(() => {
        state.mounts++;
      }, []);
      return <div data-testid="graph-canvas">Canvas</div>;
    },
}));
vi.mock("@/lib/api/generated/admin", async (original) => ({
  ...(await original<typeof api>()),
  adminKnowledgeGraph: vi.fn(),
  adminKnowledgePath: vi.fn(),
  adminKnowledgeSearch: vi.fn(),
}));
vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
const node = (suffix: string, label: string): GraphNode => ({
  id: `topic:00000000-0000-0000-0000-${suffix.padStart(12, "0")}`,
  entity_id: `00000000-0000-0000-0000-${suffix.padStart(12, "0")}`,
  kind: "topic",
  label,
  status: "active",
  subtype: "technology",
  description: `${label} description`,
});
const react = node("1", "React"),
  js = node("2", "JavaScript");
const graph: GraphOut = {
  nodes: [react, js],
  edges: [
    {
      id: "edge-one",
      source: react.id,
      target: js.id,
      kind: "uses_language",
      directed: true,
      status: "approved",
      evidence: "React uses JavaScript.",
      evidence_url: "https://react.dev/",
      role: null,
      origin: null,
      relevance: null,
    },
  ],
  catalog_counts: { topic: 2 },
  truncated: false,
  node_limit: 150,
  edge_limit: 1500,
};
beforeEach(() => {
  vi.clearAllMocks();
  state.query = "";
  state.mounts = 0;
  window.history.replaceState(null, "", "/knowledge/graph");
  const replace = window.history.replaceState.bind(window.history);
  vi.spyOn(window.history, "replaceState").mockImplementation((data, unused, url) => {
    replace(data, unused, url);
    state.query = String(url).split("?")[1] ?? "";
  });
  vi.mocked(api.adminKnowledgeGraph).mockResolvedValue(graph);
  vi.mocked(api.adminKnowledgeSearch).mockResolvedValue({ items: [react, js], total: 2 });
  vi.mocked(api.adminKnowledgePath).mockResolvedValue({
    nodes: graph.nodes,
    edges: graph.edges,
    found: true,
    truncated: false,
    max_hops: 4,
  });
});
afterEach(cleanup);

it("provides read-only inspection and accessible connection navigation", async () => {
  render(<KnowledgeGraph />);
  await screen.findByTestId("graph-canvas");
  expect(screen.queryByRole("button", { name: /refresh|approve|delete|export/i })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Connection list" }));
  fireEvent.click(
    within(screen.getByRole("list", { name: "Graph objects" })).getByRole("button", {
      name: "React · Topic",
    }),
  );
  expect(screen.getByRole("link", { name: "Open topic" }).getAttribute("href")).toBe(
    `/taxonomy/topics/${react.entity_id}`,
  );
  fireEvent.click(screen.getByRole("button", { name: "Uses language JavaScript" }));
  expect(screen.getByText("React uses JavaScript.")).toBeDefined();
  expect(screen.getByRole("link", { name: "View evidence" }).getAttribute("href")).toBe(
    "https://react.dev/",
  );
});

it("enables articles with sources and keeps filters in the URL", async () => {
  const view = render(<KnowledgeGraph />);
  await screen.findByTestId("graph-canvas");
  fireEvent.click(screen.getByRole("button", { name: "Sources" }));
  view.rerender(<KnowledgeGraph />);
  await waitFor(() =>
    expect(api.adminKnowledgeGraph).toHaveBeenLastCalledWith(
      expect.objectContaining({ layers: ["source", "article"] }),
      expect.anything(),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Show pending suggestions/ }));
  view.rerender(<KnowledgeGraph />);
  await waitFor(() =>
    expect(api.adminKnowledgeGraph).toHaveBeenLastCalledWith(
      expect.objectContaining({ include_pending: true }),
      expect.anything(),
    ),
  );
});

it("searches the catalog as you type and focuses a matching object", async () => {
  const view = render(<KnowledgeGraph />);
  await screen.findByTestId("graph-canvas");
  fireEvent.click(screen.getByRole("combobox", { name: "Find an object" }));
  fireEvent.change(screen.getByPlaceholderText("Search names, titles, aliases…"), {
    target: { value: "React" },
  });
  await waitFor(() =>
    expect(api.adminKnowledgeSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "React" }),
      expect.anything(),
    ),
  );
  fireEvent.click(await screen.findByRole("option", { name: /React/ }));
  view.rerender(<KnowledgeGraph />);
  await waitFor(() =>
    expect(api.adminKnowledgeGraph).toHaveBeenLastCalledWith(
      expect.objectContaining({ focus: react.id }),
      expect.anything(),
    ),
  );
});

it("keeps the canvas mounted while neighbours are requested", async () => {
  state.query = new URLSearchParams({ focus: react.id }).toString();
  const view = render(<KnowledgeGraph />);
  await screen.findByTestId("graph-canvas");
  const before = state.mounts;
  let finish!: (value: GraphOut) => void;
  vi.mocked(api.adminKnowledgeGraph).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  state.query += `&expand=${encodeURIComponent(js.id)}`;
  view.rerender(<KnowledgeGraph />);
  await waitFor(() => expect(api.adminKnowledgeGraph).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId("graph-canvas")).toBeDefined();
  expect(state.mounts).toBe(before);
  finish({ ...graph, nodes: [...graph.nodes, node("3", "Vue")] });
  await screen.findByText("3 topics");
  expect(state.mounts).toBe(before);
});

it("finds paths on the server using current filters and marks incomplete searches", async () => {
  state.query = new URLSearchParams({
    focus: react.id,
    to: js.id,
    mode: "path",
    direction: "outgoing",
    hops: "6",
  }).toString();
  const view = render(<KnowledgeGraph />);
  await screen.findByText("Connected through 1 link.");
  expect(api.adminKnowledgePath).toHaveBeenLastCalledWith(
    expect.objectContaining({
      from_node: react.id,
      to_node: js.id,
      direction: "outgoing",
      max_hops: 6,
    }),
    expect.anything(),
  );
  vi.mocked(api.adminKnowledgePath).mockResolvedValue({
    nodes: graph.nodes,
    edges: [],
    found: false,
    truncated: true,
    max_hops: 4,
  });
  state.query += "&pending=1";
  view.rerender(<KnowledgeGraph />);
  await screen.findByText(/Search limit reached. A connection may exist/);
});

it("recovers from a failed load and shows truthful empty states", async () => {
  vi.mocked(api.adminKnowledgeGraph).mockRejectedValueOnce(new Error("Temporary service failure"));
  vi.mocked(api.adminKnowledgeGraph).mockResolvedValue({
    ...graph,
    nodes: [],
    edges: [],
    catalog_counts: {},
  });
  render(<KnowledgeGraph />);
  await screen.findByText("Temporary service failure");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Your knowledge graph starts with active topics");
});
