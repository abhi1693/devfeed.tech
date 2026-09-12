// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ArticleTopicBrief } from "@/components/article-topic-brief";
import { topic } from "./fixtures";

vi.mock("@/lib/api", () => ({
  getTopic: vi.fn(async () => ({
    ...topic,
    description: "A *literal* pattern with [brackets](example).",
  })),
}));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
afterEach(cleanup);

it("renders topic prose without applying Markdown formatting", async () => {
  const { container } = render(
    await ArticleTopicBrief({
      topic: { ...topic, role: "primary" },
      articleSlug: "example",
    }),
  );
  expect(screen.getByText("A *literal* pattern with [brackets](example).")).toBeTruthy();
  expect(container.querySelector(".markdown, em, strong")).toBeNull();
  expect(screen.queryByRole("link", { name: "brackets" })).toBeNull();
});
