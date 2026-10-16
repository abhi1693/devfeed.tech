// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Sidebar } from "@/components/organisms/sidebar";
import { resourceHref } from "@/lib/routes";
import { resourceKeys, resources } from "@/lib/resources";

const navigation = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

const storageKey = "devfeed:admin-sidebar-expanded";
beforeEach(() => {
  navigation.pathname = "/";
  localStorage.removeItem(storageKey);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.removeItem(storageKey);
});

it("keeps the private administration groups and accessible icon links in the shared rail", () => {
  render(<Sidebar />);
  const sidebar = screen.getByRole("complementary", { name: "Administration navigation" });
  expect(sidebar.getAttribute("id")).toBe("admin-sidebar");
  expect(sidebar.getAttribute("data-sidebar-layout")).toBe("workspace");
  expect(sidebar.getAttribute("data-expanded")).toBe("false");
  const nav = within(sidebar).getByRole("navigation", { name: "Administration" });
  for (const group of ["Content", "Taxonomy", "Audience", "Partnerships", "Operations"])
    expect(within(nav).getByRole("region", { name: group })).toBeTruthy();
  for (const resource of resourceKeys) {
    const link = within(nav).getByRole("link", { name: resources[resource].label });
    expect(link.getAttribute("href")).toBe(resourceHref(resource));
    expect(link.getAttribute("title")).toBe(resources[resource].label);
    expect(link.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  }
  expect(within(nav).queryByRole("link", { name: "Feed" })).toBeNull();
  expect(within(nav).getByRole("link", { name: "Import connections" }).getAttribute("href")).toBe(
    "/partnerships/connections",
  );
});

it("uses distinct icons for every collapsed navigation destination", () => {
  render(<Sidebar />);
  const nav = screen.getByRole("navigation", { name: "Administration" });
  const icons = within(nav)
    .getAllByRole("link")
    .map((link) => {
      const icon = link.querySelector("svg");
      expect(icon).not.toBeNull();
      expect(icon?.getAttribute("width")).toBe("20");
      expect(icon?.getAttribute("height")).toBe("20");
      return icon?.getAttribute("class");
    });
  expect(new Set(icons).size).toBe(icons.length);
});

it.each([
  ["/", "Overview"],
  ["/content/articles/article-id/edit", "Articles"],
  ["/partnerships/products/product-id/related", "Products"],
  ["/workers/ingestion", "Workers"],
])("marks the current private route %s while collapsed", (pathname, label) => {
  navigation.pathname = pathname;
  render(<Sidebar />);
  const nav = screen.getByRole("navigation", { name: "Administration" });
  const active = within(nav).getByRole("link", { name: label });
  expect(active.getAttribute("aria-current")).toBe("page");
  expect(active.className).toContain("active");
  expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
});

it("persists the administrator's expansion independently of the reader and partner sidebars", async () => {
  localStorage.setItem("devfeed:partner-sidebar-expanded", "true");
  let view = render(<Sidebar />);
  const sidebar = () => screen.getByRole("complementary", { name: "Administration navigation" });
  fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(sidebar().getAttribute("data-expanded")).toBe("true");
  expect(localStorage.getItem(storageKey)).not.toBeNull();
  expect(localStorage.getItem("devfeed:partner-sidebar-expanded")).toBe("true");
  view.unmount();
  view = render(<Sidebar />);
  await waitFor(() => expect(sidebar().getAttribute("data-expanded")).toBe("true"));
  fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(sidebar().getAttribute("data-expanded")).toBe("false");
  view.unmount();
  render(<Sidebar />);
  await waitFor(() => expect(sidebar().getAttribute("data-expanded")).toBe("false"));
  localStorage.removeItem("devfeed:partner-sidebar-expanded");
});

it("closes mobile navigation with Escape and restores focus to its disclosure", () => {
  render(<Sidebar />);
  const toggle = screen.getByRole("button", { name: "Navigation" });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(toggle);
});
