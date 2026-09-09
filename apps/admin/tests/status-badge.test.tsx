// @vitest-environment jsdom
import { renderAdmin } from "./render-admin";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/molecules/status-badge";
import { BooleanIndicator } from "@/components/atoms/boolean-indicator";
import { JobLogLine } from "@/components/molecules/job-log-entry";
import { RecordTable } from "@/components/organisms/record-table";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { getRecord } from "@/lib/resource-api";
import { humanize } from "@/lib/resources";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), getRecord: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("shared colored status pills", () => {
  it.each([
    ["pending", "warning", "amber"], ["proposed", "warning", "amber"], ["queued", "warning", "amber"],
    ["approved", "success", "emerald"], ["published", "success", "emerald"], ["active", "success", "emerald"], ["succeeded", "success", "emerald"],
    ["running", "info", "sky"], ["rejected", "danger", "rose"], ["failed", "danger", "rose"],
    ["unpublished", "neutral", "slate"], ["disabled", "neutral", "slate"], ["enabled", "success", "emerald"],
    ["future_state", "neutral", "slate"], ["constructor", "neutral", "slate"],
  ])("renders %s as a readable %s pill", (value, tone, color) => {
    render(<StatusBadge value={value} />);
    const badge = screen.getByText(humanize(value));
    expect(badge.getAttribute("data-variant")).toBe(tone);
    expect(badge.className).toContain(`bg-${color}-`);
    expect(badge.className).toContain(`text-${color}-`);
    expect(badge.className).toContain(`border-${color}-200`);
    expect(badge.className).toContain("rounded-full");
    expect(badge.className).not.toContain("border-transparent");
    expect(badge.getAttribute("tabindex")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it.each([null, undefined, ""])("leaves missing status as a placeholder: %j", value => {
    render(<StatusBadge value={value} />);
    expect(screen.getByText("—")).toBeTruthy();
    expect(document.querySelector('[data-slot="badge"]')).toBeNull();
  });

  it.each([[true, "Yes", "check", "emerald"], [false, "No", "x", "rose"]] as const)("shows boolean %s as an accessible %s icon", (value, label, icon, color) => {
    render(<StatusBadge value={value} />);
    const indicator = screen.getByRole("img", { name: label });
    expect(indicator.getAttribute("title")).toBe(label);
    expect(indicator.querySelector("svg")!.getAttribute("class")).toContain(`lucide-${icon}`);
    expect(indicator.querySelector("svg")!.getAttribute("class")).toContain(`text-${color}-700`);
    expect(indicator.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByText(label)).toBeNull();
    expect(document.querySelector('[data-slot="badge"]')).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(indicator.getAttribute("tabindex")).toBeNull();
  });

  it("supports a descriptive screen-reader and hover label", () => {
    render(<BooleanIndicator value={false} label="Polling disabled" />);
    expect(screen.getByRole("img", { name: "Polling disabled" }).getAttribute("title")).toBe("Polling disabled");
  });

  it.each([true, false, null])("uses boolean icons in Enabled cells, preserving unknown values: %j", enabled => {
    renderAdmin(<RecordTable resource="sources" page={{ items: [{ id: "source-1", name: "Example", enabled, source_type: "publisher", approval_status: "approved", last_success_at: "2026-09-07T00:00:00Z" }], total: 1, limit: 25, offset: 0 }} sort="name" onChange={vi.fn()} />);
    if (enabled === null) {
      expect(screen.getByRole("cell", { name: "—" })).toBeTruthy();
      expect(screen.queryByRole("img")).toBeNull();
    } else {
      expect(screen.getByRole("img", { name: enabled ? "Yes" : "No" })).toBeTruthy();
      expect(screen.queryByText(enabled ? "Yes" : "No")).toBeNull();
    }
    expect(screen.getByText("Approved").getAttribute("data-variant")).toBe("success");
  });

  it("escapes unknown labels rather than interpreting them as markup", () => {
    const text = '<img src="x" onerror="alert(1)">';
    render(<StatusBadge value={text} />);
    expect(screen.getByText(text).getAttribute("data-variant")).toBe("neutral");
    expect(document.querySelector("img")).toBeNull();
  });

  it("colors article review and publication independently", () => {
    renderAdmin(<RecordTable resource="articles" page={{ items: [{ id: "article-1", title: "Example", review_status: "pending", publication_status: "unpublished" }], total: 1, limit: 25, offset: 0 }} sort="-discovered_at" onChange={vi.fn()} />);
    expect(screen.getByText("Pending").getAttribute("data-variant")).toBe("warning");
    expect(screen.getByText("Unpublished").getAttribute("data-variant")).toBe("neutral");
  });

  it.each(["ingestion-jobs", "article-jobs", "image-jobs", "source-jobs", "analysis-jobs", "notification-jobs"] as const)("uses the same lifecycle colors in %s tables", resource => {
    const states = { queued: "warning", running: "info", succeeded: "success", failed: "danger" };
    renderAdmin(<RecordTable resource={resource} page={{ items: Object.keys(states).map(status => ({ id: `job-${status}`, status, attempts: 1 })), total: 4, limit: 25, offset: 0 }} sort="-created_at" onChange={vi.fn()} />);
    for (const [status, tone] of Object.entries(states)) expect(screen.getByText(humanize(status)).getAttribute("data-variant")).toBe(tone);
  });

  it.each(["sources", "topics", "articles"] as const)("uses colored statuses on %s detail pages", async resource => {
    vi.mocked(getRecord).mockResolvedValue({ id: "record-1", name: "Example", title: "Example", status: "active", approval_status: "approved", review_status: "pending", publication_status: "unpublished", enabled: false, publication_blockers: [], sources: [], topics: [], tags: [] });
    render(<ResourceDetail resource={resource} id="record-1" />);
    await screen.findByRole("heading", { level: 1, name: "Example" });
    for (const [status, tone] of Object.entries({ active: "success", approved: "success", pending: "warning", unpublished: "neutral" })) {
      expect(screen.getAllByText(humanize(status)).every(badge => badge.getAttribute("data-variant") === tone)).toBe(true);
    }
    if (resource === "sources") expect(screen.getByRole("img", { name: "No" }).querySelector("svg")!.getAttribute("class")).toContain("text-rose-700");
  });

  it.each([["DEBUG", "neutral"], ["INFO", "info"], ["WARNING", "warning"], ["ERROR", "danger"], ["CRITICAL", "danger"]] as const)("colors %s runtime log pills without changing severity labels", (level, tone) => {
    render(<ol><JobLogLine entry={{ id: "1-0", timestamp: "2026-09-07T00:00:00Z", message: "Example event", level, fields: {} }} /></ol>);
    expect(screen.getByText(level).getAttribute("data-variant")).toBe(tone);
  });
});
