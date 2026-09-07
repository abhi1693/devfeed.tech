// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { RecordActions } from "@/components/molecules/record-actions";
import { resourceKeys, resources } from "@/lib/resources";

afterEach(cleanup);
const labels = () => within(screen.getByRole("group", { name: "Record actions" })).getAllByRole("link").map(link => link.getAttribute("aria-label") ?? link.textContent);

describe("record action ordering and icons", () => {
  it("puts source operations before Edit and Delete, with unchanged workflow links", () => {
    render(<RecordActions resource="sources" id="source-1" detail />);
    expect(labels()).toEqual(["Fetch feed", "Review source", "Edit", "Delete"]);
    for (const [label, route] of [["Fetch feed", "fetch"], ["Review source", "review"], ["Edit", "edit"], ["Delete", "delete"]]) {
      expect(screen.getByRole("link", { name: label }).getAttribute("href")).toBe(`/content/sources/source-1/${route}`);
    }
  });

  it("puts article classification and review before Edit and Delete", () => {
    render(<RecordActions resource="articles" id="article-1" detail />);
    expect(labels()).toEqual(["Classify", "Review / publish", "Edit", "Delete"]);
    expect(screen.getByRole("link", { name: "Classify" }).getAttribute("href")).toBe("/content/articles/article-1/classify");
    expect(screen.getByRole("link", { name: "Review / publish" }).getAttribute("href")).toBe("/content/articles/article-1/review");
  });

  it.each(resourceKeys.filter(resource => !resources[resource].readonly))("keeps Delete last and Edit before it in %s row and detail actions", resource => {
    const { rerender } = render(<RecordActions resource={resource} id="record-1" />);
    expect(labels()).toEqual(["Edit", "Delete"]);
    expect(screen.queryByRole("link", { name: "View" })).toBeNull();
    for (const link of screen.getAllByRole("link")) {
      expect(link.textContent).toBe("");
      expect(link.getAttribute("data-size")).toBe("icon-sm");
      expect(link.querySelectorAll("svg")).toHaveLength(1);
    }
    expect(screen.queryByRole("link", { name: "Fetch feed" })).toBeNull();
    rerender(<RecordActions resource={resource} id="record-1" detail />);
    expect(labels().slice(-2)).toEqual(["Edit", "Delete"]);
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("data-slot")).toBe("button");
      expect(link.querySelectorAll("svg")).toHaveLength(1);
      expect(link.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    }
    expect(screen.getByRole("link", { name: "Delete" }).getAttribute("data-variant")).toBe("destructive-ghost");
  });

  it.each(resourceKeys.filter(resource => resources[resource].readonly))("does not offer editing, deletion, or unrelated operations for %s", resource => {
    const { rerender } = render(<RecordActions resource={resource} id="job-1" />);
    expect(screen.queryByRole("group")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    rerender(<RecordActions resource={resource} id="job-1" detail />);
    expect(screen.queryByRole("group")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("keeps record IDs encoded in action URLs", () => {
    render(<RecordActions resource="sources" id="source /1" detail />);
    expect(screen.getByRole("link", { name: "Delete" }).getAttribute("href")).toBe("/content/sources/source%20%2F1/delete");
    expect(screen.getByRole("link", { name: "Fetch feed" }).getAttribute("href")).toBe("/content/sources/source%20%2F1/fetch");
  });

  it("shows labels for icon-only actions on keyboard focus", async () => {
    render(<RecordActions resource="sources" id="source-1" />);
    fireEvent.focus(screen.getByRole("link", { name: "Edit" }));
    expect((await screen.findByRole("tooltip")).textContent).toBe("Edit");
    fireEvent.blur(screen.getByRole("link", { name: "Edit" }));
    fireEvent.focus(screen.getByRole("link", { name: "Delete" }));
    expect((await screen.findByRole("tooltip")).textContent).toBe("Delete");
  });
});
