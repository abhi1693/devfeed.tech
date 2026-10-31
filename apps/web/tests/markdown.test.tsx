// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Markdown } from "@devfeed/ui/markdown";
afterEach(cleanup);
it("renders Markdown emphasis, lists and links without raw HTML or remote badges", () => {
  const view = render(
    <Markdown>
      {
        "**CI/CD** automates delivery.\n\n- [Integration](https://github.com/topics/continuous-integration)\n- Delivery\n\n![Badge](https://example.com/badge.svg)\n<script>alert(1)</script>\n[Unsafe](javascript:alert(1))"
      }
    </Markdown>,
  );
  expect(view.container.querySelector("strong")?.textContent).toBe("CI/CD");
  expect(screen.getByRole("link", { name: "Integration" }).getAttribute("href")).toBe(
    "https://github.com/topics/continuous-integration",
  );
  expect(view.container.querySelectorAll("li")).toHaveLength(2);
  expect(view.container.querySelector("script, img")).toBeNull();
  expect(screen.queryByRole("link", { name: "Unsafe" })).toBeNull();
});
it("keeps catalog summaries free of nested links", () => {
  render(<Markdown compact>{"[Python](https://python.org) is **readable**."}</Markdown>);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("readable")).toBeTruthy();
});

it("reuses syntax across full and compact rendering without reusing link policy", async () => {
  const { markdownParseCache } = await import("../../../packages/ui/src/markdown-cache");
  markdownParseCache.clear();
  const text = "[Python](https://python.org) and **delivery**.\n\n![Hidden](https://x.test/a.png)";
  const first = render(<Markdown>{text}</Markdown>);
  expect(first.container.querySelector("a")?.getAttribute("href")).toBe("https://python.org");
  first.unmount();
  const compact = render(<Markdown compact>{text}</Markdown>);
  expect(compact.container.querySelector("a, img")).toBeNull();
  expect(compact.container.querySelector("strong")?.textContent).toBe("delivery");
  expect(markdownParseCache.stats()).toMatchObject({ misses: 1, hits: 1 });
});

it("reparses changed text and keeps unsafe content blocked on cache hits", async () => {
  const { markdownParseCache } = await import("../../../packages/ui/src/markdown-cache");
  markdownParseCache.clear();
  const text = "<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))\n\n**Approved**";
  const first = render(<Markdown>{text}</Markdown>);
  const expected = first.container.innerHTML;
  first.unmount();
  const second = render(<Markdown>{text}</Markdown>);
  expect(second.container.innerHTML).toBe(expected);
  expect(second.container.querySelector("script, img, a[href]")).toBeNull();
  second.rerender(<Markdown>{"**Replacement**"}</Markdown>);
  expect(second.container.querySelector("strong")?.textContent).toBe("Replacement");
  expect(markdownParseCache.stats()).toMatchObject({ misses: 2, hits: 1 });
});
