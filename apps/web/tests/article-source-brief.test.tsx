// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ArticleSourceBrief } from "@/components/article-source-brief";
vi.mock("@/components/source-follow", () => ({
  SourceFollow: () => <button>Follow source</button>,
}));
afterEach(cleanup);
it("groups source identity, description and following in one section", () => {
  render(
    <ArticleSourceBrief
      articleSlug="article"
      source={{
        id: "source",
        slug: "grafana",
        name: "Grafana Labs",
        description: "Updates from the Grafana team.",
        logo_url: null,
        website_url: "https://grafana.com",
      }}
    />,
  );
  expect(screen.getByRole("region", { name: "About Grafana Labs" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Grafana Labs" }).getAttribute("href")).toBe(
    "/sources/grafana",
  );
  expect(screen.getByText("Updates from the Grafana team.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Follow source" })).toBeTruthy();
});
