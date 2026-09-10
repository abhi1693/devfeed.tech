// @vitest-environment jsdom
import { renderAdmin } from "./render-admin";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { RecordTable } from "@/components/organisms/record-table";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { getRecord } from "@/lib/resource-api";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), getRecord: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("read-only language labels", () => {
  it.each([
    ["en", "English"],
    ["en-us", "English (United States)"],
    ["fr", "French"],
    ["ja", "Japanese"],
    ["zh-hant", "Chinese (Traditional)"],
    ["yue", "Cantonese"],
    ["zz", "zz"],
    ["not_a_language", "not_a_language"],
    [null, "—"],
    [undefined, "—"],
    ["", "—"],
  ] as const)("shows %j as %j in list and related-object tables without changing stored codes", (language, label) => {
    const article = { id: "article-1", title: "Example article", language, review_status: "approved", publication_status: "published", discovered_at: "2026-09-07T00:00:00Z" };
    renderAdmin(<RecordTable resource="articles" page={{ items: [article], total: 1, limit: 25, offset: 0 }} sort="-discovered_at" onChange={vi.fn()} />);
    expect(screen.getByRole("cell", { name: label })).toBeTruthy();
    expect(article.language).toBe(language);
  });

  it.each(["sources", "articles"] as const)("uses the same regional language name on %s detail pages", async resource => {
    vi.mocked(getRecord).mockResolvedValue({ id: "record-1", name: "Example source", title: "Example article", language: "en-us", publication_blockers: [], sources: [], topics: [], tags: [] });
    renderAdmin(<ResourceDetail resource={resource} id="record-1" />);
    await screen.findByText("English (United States)");
    const label = screen.getByText("Language", { selector: "dt" });
    expect(label.nextElementSibling!.textContent).toBe("English (United States)");
    expect(screen.queryByText("en-us", { exact: true })).toBeNull();
  });
});
