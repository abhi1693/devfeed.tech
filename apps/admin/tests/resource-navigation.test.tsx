// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ResourceGroup } from "@/components/organisms/resource-group";
import {
  resourceGroups,
  resourceKeys,
  resources,
  type Resource,
  type RouteGroup,
} from "@/lib/resources";
import { resourceHref, resourceTrail, resolveAdminRoute } from "@/lib/routes";

afterEach(cleanup);

const contentTrail = [{ label: "Content", href: "/content" }];
const taxonomyTrail = [{ label: "Taxonomy", href: "/taxonomy" }];
const jobsTrail = [{ label: "Jobs", href: "/jobs" }];
const enrichmentTrail = [...jobsTrail, { label: "Enrichment jobs", href: "/jobs/enrichment" }];
const navigationCases: {
  resource: Resource;
  href: string;
  groups: RouteGroup[];
  breadcrumbs: { label: string; href: string }[];
}[] = [
  { resource: "users", href: "/users", groups: [], breadcrumbs: [] },
  {
    resource: "articles",
    href: "/content/articles",
    groups: ["content"],
    breadcrumbs: contentTrail,
  },
  {
    resource: "sources",
    href: "/content/sources",
    groups: ["content"],
    breadcrumbs: contentTrail,
  },
  {
    resource: "topics",
    href: "/taxonomy/topics",
    groups: ["taxonomy"],
    breadcrumbs: taxonomyTrail,
  },
  {
    resource: "tags",
    href: "/taxonomy/tags",
    groups: ["taxonomy"],
    breadcrumbs: taxonomyTrail,
  },
  {
    resource: "topic-relations",
    href: "/taxonomy/relationships",
    groups: ["taxonomy"],
    breadcrumbs: taxonomyTrail,
  },
  {
    resource: "ingestion-jobs",
    href: "/jobs/ingestion",
    groups: ["jobs"],
    breadcrumbs: jobsTrail,
  },
  {
    resource: "article-jobs",
    href: "/jobs/enrichment/articles",
    groups: ["jobs", "enrichment"],
    breadcrumbs: enrichmentTrail,
  },
  {
    resource: "image-jobs",
    href: "/jobs/enrichment/images",
    groups: ["jobs", "enrichment"],
    breadcrumbs: enrichmentTrail,
  },
  {
    resource: "source-jobs",
    href: "/jobs/enrichment/sources",
    groups: ["jobs", "enrichment"],
    breadcrumbs: enrichmentTrail,
  },
  {
    resource: "analysis-jobs",
    href: "/jobs/analysis",
    groups: ["jobs"],
    breadcrumbs: jobsTrail,
  },
  {
    resource: "notification-jobs",
    href: "/jobs/notifications",
    groups: ["jobs"],
    breadcrumbs: jobsTrail,
  },
];

const groupCases: { group: RouteGroup; title: string; resources: Resource[]; trail: string[] }[] = [
  { group: "content", title: "Content", resources: ["articles", "sources"], trail: ["Home"] },
  {
    group: "taxonomy",
    title: "Taxonomy",
    resources: ["topics", "tags", "topic-relations"],
    trail: ["Home"],
  },
  {
    group: "jobs",
    title: "Jobs",
    resources: [
      "ingestion-jobs",
      "article-jobs",
      "image-jobs",
      "source-jobs",
      "analysis-jobs",
      "notification-jobs",
    ],
    trail: ["Home"],
  },
  {
    group: "enrichment",
    title: "Enrichment jobs",
    resources: ["article-jobs", "image-jobs", "source-jobs"],
    trail: ["Home", "Jobs"],
  },
];

function cardLinks() {
  return screen.getAllByRole("link").filter((link) => !link.closest("nav"));
}

describe("resource navigation definitions", () => {
  it("covers every registered resource without adding users to a resource group", () => {
    expect(navigationCases.map(({ resource }) => resource)).toEqual(resourceKeys);
    expect(resources.users.navigation.groups).toEqual([]);
    expect(resourceTrail("users")).toEqual([]);
  });

  it.each(navigationCases)("preserves $resource navigation and breadcrumb order", (expected) => {
    const navigation = resources[expected.resource].navigation;
    expect(navigation.href).toBe(expected.href);
    expect(navigation.groups).toEqual(expected.groups);
    expect(navigation.breadcrumbs.map(({ label, href }) => ({ label, href }))).toEqual(
      expected.breadcrumbs,
    );
    expect(resourceHref(expected.resource)).toBe(expected.href);
    expect(resourceTrail(expected.resource)).toEqual(expected.breadcrumbs);
  });

  it("retains explicitly declared breadcrumbs when a resource moves under another URL prefix", () => {
    const navigation = resources.articles.navigation;
    resources.articles.navigation = { ...navigation, href: "/jobs/enrichment/articles-renamed" };
    try {
      expect(resourceHref("articles")).toBe("/jobs/enrichment/articles-renamed");
      expect(resourceTrail("articles")).toEqual(contentTrail);
      expect(resolveAdminRoute(["jobs", "enrichment", "articles-renamed"])).toEqual({
        view: "list",
        resource: "articles",
      });

      const view = render(<ResourceGroup group="content" />);
      expect(cardLinks().map((link) => link.getAttribute("href"))).toEqual([
        "/jobs/enrichment/articles-renamed",
        "/content/sources",
      ]);
      view.unmount();
      render(<ResourceGroup group="enrichment" />);
      expect(cardLinks()).toHaveLength(3);
      expect(screen.queryByText("Articles")).toBeNull();
    } finally {
      resources.articles.navigation = navigation;
    }
  });

  it("uses the declared breadcrumb chain even when its links have unrelated URL prefixes", () => {
    const navigation = resources.tags.navigation;
    const breadcrumbs = [
      { label: "Editorial", href: "/workspace/editorial" },
      { label: "Classification", href: "/settings/classification" },
    ];
    resources.tags.navigation = { ...navigation, breadcrumbs };
    try {
      expect(resourceTrail("tags")).toEqual(breadcrumbs);
      expect(resourceHref("tags")).toBe("/taxonomy/tags");
    } finally {
      resources.tags.navigation = navigation;
    }
  });
});

describe("resource group pages", () => {
  it.each(groupCases)(
    "renders $group heading, parent trail, and ordered resource links",
    (expected) => {
      render(<ResourceGroup group={expected.group} />);
      expect(screen.getByRole("heading", { name: expected.title, level: 1 })).toBeTruthy();
      expect(document.title).toBe(`${expected.title} · DevFeed Admin`);
      const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
      expect(
        within(breadcrumb)
          .getAllByRole("link")
          .map((link) => link.textContent),
      ).toEqual(expected.trail);
      const links = cardLinks();
      expect(links.map((link) => link.getAttribute("href"))).toEqual(
        expected.resources.map((resource) => resourceHref(resource)),
      );
      links.forEach((link, index) => {
        const definition = resources[expected.resources[index]];
        expect(within(link).getByText(definition.label)).toBeTruthy();
        expect(within(link).getByText(definition.description)).toBeTruthy();
      });
      expect(screen.queryByText("Users")).toBeNull();
      if (expected.group === "enrichment")
        expect(within(breadcrumb).getByRole("link", { name: "Jobs" }).getAttribute("href")).toBe(
          "/jobs",
        );
    },
  );

  it("keeps group membership independent of the group's route prefix", () => {
    const definition = resourceGroups.jobs;
    const href = definition.href;
    definition.href = "/workspace/operations";
    try {
      expect(resolveAdminRoute(["workspace", "operations"])).toEqual({
        view: "group",
        group: "jobs",
      });
      expect(resourceTrail("ingestion-jobs")).toEqual([
        { label: "Jobs", href: "/workspace/operations" },
      ]);
      expect(resourceTrail("article-jobs")).toEqual([
        { label: "Jobs", href: "/workspace/operations" },
        { label: "Enrichment jobs", href: "/jobs/enrichment" },
      ]);
      const view = render(<ResourceGroup group="jobs" />);
      expect(screen.getByRole("heading", { name: "Jobs", level: 1 })).toBeTruthy();
      expect(cardLinks().map((link) => link.getAttribute("href"))).toEqual([
        "/jobs/ingestion",
        "/jobs/enrichment/articles",
        "/jobs/enrichment/images",
        "/jobs/enrichment/sources",
        "/jobs/analysis",
        "/jobs/notifications",
      ]);
      view.unmount();
      render(<ResourceGroup group="enrichment" />);
      expect(cardLinks()).toHaveLength(3);
      expect(
        within(screen.getByRole("navigation", { name: "Breadcrumb" }))
          .getByRole("link", { name: "Jobs" })
          .getAttribute("href"),
      ).toBe("/workspace/operations");
    } finally {
      definition.href = href;
    }
  });
});
