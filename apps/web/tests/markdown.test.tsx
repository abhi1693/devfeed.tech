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
