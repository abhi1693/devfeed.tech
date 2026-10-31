// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Root } from "mdast";
import { createMarkdownParseCache } from "../../../packages/ui/src/markdown-cache";

afterEach(cleanup);

it("gives every downstream transformer an independent tree", () => {
  const cache = createMarkdownParseCache();
  function append() {
    return (tree: Root) => {
      tree.children.push({ type: "paragraph", children: [{ type: "text", value: "Each render" }] });
    };
  }
  const renderText = () =>
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm, cache.plugin, append]}>
        {"| GFM | Table |\n| --- | --- |\n| a | b |"}
      </ReactMarkdown>,
    );
  const first = renderText();
  const expected = first.container.innerHTML;
  expect(first.container.querySelectorAll("table")).toHaveLength(1);
  first.unmount();
  const second = renderText();
  expect(second.container.innerHTML).toBe(expected);
  expect(cache.stats()).toMatchObject({ entries: 1, misses: 1, hits: 1 });
});

it("evicts least-recent syntax and enforces both payload and entry budgets", () => {
  const cache = createMarkdownParseCache({ maxEntries: 2, maxBytes: 5000 });
  const parse = (text: string) => {
    const view = render(<ReactMarkdown remarkPlugins={[cache.plugin]}>{text}</ReactMarkdown>);
    view.unmount();
  };
  parse("one");
  parse("two");
  parse("one");
  parse("three");
  expect(cache.stats()).toMatchObject({ entries: 2, hits: 1, misses: 3 });
  parse("two");
  expect(cache.stats()).toMatchObject({ entries: 2, hits: 1, misses: 4 });
  expect(cache.stats().bytes).toBeLessThanOrEqual(5000);
  cache.clear();
  expect(cache.stats()).toEqual({ entries: 0, bytes: 0, hits: 0, misses: 0, bypasses: 0 });
});

it.each([{ maxBytes: 1 }, { maxEntries: 0 }, { maxSourceLength: 1 }])(
  "renders normally when syntax exceeds its retention budget %j",
  (limits) => {
    const cache = createMarkdownParseCache(limits);
    const view = render(<ReactMarkdown remarkPlugins={[cache.plugin]}>**Budgeted**</ReactMarkdown>);
    expect(view.container.querySelector("strong")?.textContent).toBe("Budgeted");
    expect(cache.stats().entries).toBe(0);
    expect(cache.stats().bypasses).toBe(1);
  },
);

it("evicts by bytes before the entry limit and preserves an older hit after an oversized bypass", () => {
  const cache = createMarkdownParseCache({ maxEntries: 128, maxBytes: 1300, maxSourceLength: 100 });
  const parse = (text: string) => {
    const view = render(<ReactMarkdown remarkPlugins={[cache.plugin]}>{text}</ReactMarkdown>);
    view.unmount();
  };
  parse("first short paragraph");
  const firstBytes = cache.stats().bytes;
  parse("second short paragraph");
  expect(cache.stats().entries).toBe(1);
  expect(cache.stats().bytes).toBeLessThanOrEqual(1300);
  expect(firstBytes).toBeGreaterThan(0);
  parse("x".repeat(101));
  parse("second short paragraph");
  expect(cache.stats()).toMatchObject({ entries: 1, hits: 1, bypasses: 1 });
});
