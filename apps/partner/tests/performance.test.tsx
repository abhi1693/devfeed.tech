// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PartnerPerformance } from "@devfeed/ui/partner-performance";
import { performanceCsv, performanceDays } from "@devfeed/ui/performance";
import type { WorkspaceDashboard } from "../src/lib/api/generated/models";

afterEach(cleanup);

beforeEach(() => {
  // Supply browser layout so the real responsive Recharts SVG renders in jsdom.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    if (this.id === "recharts_measurement_span")
      return new DOMRect(0, 0, (this.textContent?.length ?? 0) * 6, 12);
    return new DOMRect(0, 0, 600, 192);
  });
});

function dashboard(overrides: Partial<WorkspaceDashboard> = {}): WorkspaceDashboard {
  return {
    account_total: 2,
    start: "2026-10-01",
    end: "2026-10-03",
    totals: { impressions: 1000, clicks: 11, ctr: 1.1, measured_days: 2 },
    assets: [],
    asset_total: 0,
    trend: [
      { day: "2026-10-01", impressions: 10, clicks: 1 },
      { day: "2026-10-03", impressions: 990, clicks: 10 },
    ],
    last_updated_at: null,
    ...overrides,
  };
}

const measuredFormats = [
  { ad_format: "feed", impressions: 60, clicks: 6, ctr: 10, measured_days: 2 },
  { ad_format: "header_sponsorship", impressions: 20, clicks: 4, ctr: 20, measured_days: 2 },
  { ad_format: "engagement", impressions: 20, clicks: 0, ctr: 0, measured_days: 1 },
] satisfies NonNullable<WorkspaceDashboard["format_totals"]>;

describe("reporting days", () => {
  it("keeps an unmeasured day distinct from a recorded zero", () => {
    const rows = performanceDays(
      dashboard({
        trend: [
          { day: "2026-10-01", impressions: 0, clicks: 0 },
          { day: "2026-10-03", impressions: 20, clicks: 2 },
        ],
      }),
    );
    expect(rows).toEqual([
      { day: "2026-10-01", impressions: 0, clicks: 0, ctr: null },
      { day: "2026-10-02", impressions: null, clicks: null, ctr: null },
      { day: "2026-10-03", impressions: 20, clicks: 2, ctr: 10 },
    ]);
  });

  it("uses the requested UTC date range rather than stretching to observed rows", () => {
    const rows = performanceDays(
      dashboard({
        start: "2026-12-31",
        end: "2027-01-02",
        trend: [{ day: "2027-01-01", impressions: 8, clicks: 1 }],
      }),
    );
    expect(rows.map((row) => row.day)).toEqual(["2026-12-31", "2027-01-01", "2027-01-02"]);
    expect(rows[1].ctr).toBe(12.5);
    expect(rows[0].impressions).toBeNull();
    expect(rows[2].impressions).toBeNull();
  });

  it("exports unknown counts and undefined CTR as empty CSV cells", () => {
    expect(
      performanceCsv(
        performanceDays(dashboard({ trend: [{ day: "2026-10-01", impressions: 0, clicks: 0 }] })),
      ),
    ).toBe(
      "Date (UTC),Impressions,Clicks,CTR (%)\r\n2026-10-01,0,0,\r\n2026-10-02,,,\r\n2026-10-03,,,",
    );
  });
});

describe("partner performance", () => {
  it("shows aggregate CTR rather than averaging daily percentages", () => {
    render(<PartnerPerformance data={dashboard()} />);
    const metric = screen.getByRole("region", { name: "Click-through rate" });
    expect(within(metric).getByText("1.10%")).toBeTruthy();
    expect(screen.queryByText("5.51%")).toBeNull();
    expect(screen.getByText(/2 of 3 days have measurements/)).toBeTruthy();
    expect(screen.getByText(/Missing days are gaps/)).toBeTruthy();
  });

  it("plots impressions and clicks together on labeled zero-based scales with a shared tooltip", async () => {
    const user = userEvent.setup();
    render(
      <PartnerPerformance
        data={dashboard({
          end: "2026-10-05",
          totals: { impressions: 1000, clicks: 11, ctr: 1.1, measured_days: 4 },
          trend: [
            { day: "2026-10-01", impressions: 100, clicks: 0 },
            { day: "2026-10-02", impressions: 200, clicks: 2 },
            { day: "2026-10-04", impressions: 300, clicks: 3 },
            { day: "2026-10-05", impressions: 400, clicks: 6 },
          ],
        })}
      />,
    );
    const delivery = screen.getByRole("region", { name: "Delivery trend" });
    const chart = within(delivery).getByRole("figure", { name: "Impressions and clicks" });
    const legend = within(delivery).getByRole("list", { name: "Chart series" });
    expect(within(legend).getByText("Impressions (left)")).toBeTruthy();
    expect(within(legend).getByText("Clicks (right)")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Trend metric" })).toBeNull();
    expect(within(delivery).queryByRole("button")).toBeNull();

    const lines = chart.querySelectorAll<SVGPathElement>(".recharts-line-curve");
    expect(lines).toHaveLength(2);
    expect(lines[0].getAttribute("stroke")).not.toBe(lines[1].getAttribute("stroke"));
    expect(lines[0].getAttribute("stroke-dasharray")).toBeNull();
    expect(lines[1].getAttribute("stroke-dasharray")).toBeTruthy();
    // Both observed runs produce their own segment; the unknown middle day is a gap.
    for (const line of lines) expect(line.getAttribute("d")?.match(/M/g)).toHaveLength(2);
    const series = chart.querySelectorAll(".recharts-line-dots");
    for (const line of series) expect(line.querySelectorAll(".recharts-line-dot")).toHaveLength(4);

    const axes = chart.querySelectorAll(".recharts-yAxis-tick-labels");
    expect(axes).toHaveLength(2);
    const ticks = Array.from(axes, (axis) =>
      Array.from(axis.querySelectorAll(".recharts-cartesian-axis-tick-value")),
    );
    const scales = ticks.map((axis) => axis.map((tick) => Number(tick.textContent)));
    expect(Math.min(...scales[0])).toBe(0);
    expect(Math.min(...scales[1])).toBe(0);
    expect(Math.max(...scales[0])).toBeGreaterThanOrEqual(400);
    expect(Math.max(...scales[1])).toBeGreaterThanOrEqual(6);
    expect(Math.max(...scales[1])).toBeLessThan(400);
    expect(Number(ticks[0][0].getAttribute("x"))).toBeLessThan(
      Number(ticks[1][0].getAttribute("x")),
    );

    // Recharts' accessible keyboard navigation exposes both values for the same date.
    await user.tab();
    await user.tab();
    expect(document.activeElement).toBe(within(chart).getByRole("application"));
    const tooltip = await within(chart).findByRole("status");
    expect(within(tooltip).getByText("Oct 1", { exact: true })).toBeTruthy();
    expect(within(tooltip).getByText("Impressions", { exact: true })).toBeTruthy();
    expect(within(tooltip).getByText("100", { exact: true })).toBeTruthy();
    expect(within(tooltip).getByText("Clicks", { exact: true })).toBeTruthy();
    expect(within(tooltip).getByText("0", { exact: true })).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(within(tooltip).getByText("Oct 2", { exact: true })).toBeTruthy());
    expect(within(tooltip).getByText("200", { exact: true })).toBeTruthy();
    expect(within(tooltip).getByText("2", { exact: true })).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(within(chart).queryByRole("status")).toBeNull());
    await user.keyboard("{ArrowRight}");
    const measuredTooltip = await within(chart).findByRole("status");
    expect(within(measuredTooltip).getByText("Oct 4", { exact: true })).toBeTruthy();
    expect(within(measuredTooltip).getByText("300", { exact: true })).toBeTruthy();
    expect(within(measuredTooltip).getByText("3", { exact: true })).toBeTruthy();
    expect(screen.getByRole("figure", { name: "Click-through rate" })).toBeTruthy();
  });

  it("does not present an unmeasured workspace as having delivered zero", () => {
    render(
      <PartnerPerformance
        data={dashboard({
          trend: [],
          totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 0 },
        })}
      />,
    );
    expect(within(screen.getByRole("region", { name: "Impressions" })).getByText("—")).toBeTruthy();
    expect(
      screen.getByText("Your delivery trend will appear when measurements arrive."),
    ).toBeTruthy();
    expect(
      screen.getByText("Click-through rate appears after the first measured impression."),
    ).toBeTruthy();
    const formats = screen.getByRole("region", { name: "Format comparison" });
    expect(
      within(formats).getByText(
        "Format comparisons appear after your first delivery measurements.",
      ),
    ).toBeTruthy();
    expect(within(formats).queryByRole("figure")).toBeNull();
  });

  it("shows recorded zero counts and leaves zero-impression CTR unavailable", () => {
    render(
      <PartnerPerformance
        data={dashboard({
          trend: [{ day: "2026-10-01", impressions: 0, clicks: 0 }],
          totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 1 },
        })}
      />,
    );
    expect(within(screen.getByRole("region", { name: "Impressions" })).getByText("0")).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Click-through rate" })).getByText("—"),
    ).toBeTruthy();
    expect(screen.getByRole("figure", { name: "Impressions and clicks" })).toBeTruthy();
  });

  it("exports the full reporting range without account names or made-up zeros", () => {
    render(<PartnerPerformance data={dashboard()} />);
    const download = screen.getByRole("link", { name: "Export daily CSV" });
    expect(download.getAttribute("download")).toBe("devfeed-performance-2026-10-01-2026-10-03.csv");
    const csv = decodeURIComponent(download.getAttribute("href")!.split(",")[1]);
    expect(csv).toContain("2026-10-02,,,");
    expect(csv).toContain("2026-10-01,10,1,10.00");
    expect(csv).not.toContain("Shipyard");
  });

  it("compares formats on the dashboard and links to the user-wide report and same period", () => {
    render(
      <PartnerPerformance
        data={dashboard({ end: "2026-10-07" })}
        compact
        performanceHref="/performance?days=7"
      />,
    );
    expect(screen.getByRole("link", { name: "View performance" }).getAttribute("href")).toBe(
      "/performance?days=7",
    );
    expect(screen.getByRole("region", { name: "Delivery trend" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Click-through trend" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Format comparison" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Export daily CSV" })).toBeNull();
  });

  it("compares measured impression shares while keeping each format's CTR separate", () => {
    render(
      <PartnerPerformance
        data={dashboard({
          format_totals: [
            ...measuredFormats,
            {
              ad_format: "sponsored_content",
              impressions: 999,
              clicks: 99,
              ctr: 9.91,
              measured_days: 0,
            },
          ],
        })}
      />,
    );
    const formats = screen.getByRole("region", { name: "Format comparison" });
    const selector = within(formats).getByRole("group", { name: "Format metric" });
    expect(
      within(selector).getByRole("button", { name: "Impressions" }).getAttribute("aria-pressed"),
    ).toBe("true");
    const figure = within(formats).getByRole("figure", { name: "Impression share by format" });
    expect(figure.querySelectorAll(".recharts-pie-sector")).toHaveLength(3);
    const rows = within(within(formats).getByRole("list", { name: "Format totals" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(3);
    const feed = rows.find((row) => within(row).queryByText("Feed"))!;
    expect(within(feed).getByLabelText("60 impressions")).toBeTruthy();
    expect(within(feed).getByText("60.0% · 10.00% CTR")).toBeTruthy();
    const header = rows.find((row) => within(row).queryByText("Header sponsorship"))!;
    expect(within(header).getByLabelText("20 impressions")).toBeTruthy();
    expect(within(header).getByText("20.0% · 20.00% CTR")).toBeTruthy();
    expect(within(formats).queryByText("Sponsored content")).toBeNull();
  });

  it("switches to click shares without dropping measured formats that have no clicks", async () => {
    const user = userEvent.setup();
    render(<PartnerPerformance data={dashboard({ format_totals: measuredFormats })} />);
    const formats = screen.getByRole("region", { name: "Format comparison" });
    const selector = within(formats).getByRole("group", { name: "Format metric" });
    await user.click(within(selector).getByRole("button", { name: "Clicks" }));
    expect(
      within(selector).getByRole("button", { name: "Clicks" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      within(selector).getByRole("button", { name: "Impressions" }).getAttribute("aria-pressed"),
    ).toBe("false");
    expect(
      within(formats).queryByRole("figure", { name: "Impression share by format" }),
    ).toBeNull();
    const figure = within(formats).getByRole("figure", { name: "Click share by format" });
    expect(figure.querySelectorAll(".recharts-pie-sector")).toHaveLength(2);
    const rows = within(within(formats).getByRole("list", { name: "Format totals" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(3);
    const header = rows.find((row) => within(row).queryByText("Header sponsorship"))!;
    expect(within(header).getByLabelText("4 clicks")).toBeTruthy();
    expect(within(header).getByText("40.0% · 20.00% CTR")).toBeTruthy();
    const engagement = rows.find((row) => within(row).queryByText("Engagement"))!;
    expect(within(engagement).getByLabelText("0 clicks")).toBeTruthy();
    expect(within(engagement).getByText("0.0% · 0.00% CTR")).toBeTruthy();
  });

  it("shows measured zero clicks without inventing a distribution", async () => {
    const user = userEvent.setup();
    render(
      <PartnerPerformance
        data={dashboard({
          format_totals: [
            { ad_format: "feed", impressions: 50, clicks: 0, ctr: 0, measured_days: 1 },
            {
              ad_format: "engagement",
              impressions: 0,
              clicks: 0,
              ctr: null,
              measured_days: 1,
            },
          ],
        })}
      />,
    );
    const formats = screen.getByRole("region", { name: "Format comparison" });
    const selector = within(formats).getByRole("group", { name: "Format metric" });
    await user.click(within(selector).getByRole("button", { name: "Clicks" }));
    expect(within(formats).getByText("No clicks recorded in this period.")).toBeTruthy();
    expect(within(formats).queryByRole("figure")).toBeNull();
    expect(formats.querySelectorAll(".recharts-pie-sector")).toHaveLength(0);
    const rows = within(within(formats).getByRole("list", { name: "Format totals" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(within(row).getByLabelText("0 clicks")).toBeTruthy();
      expect(within(row).getByText(/^— · /)).toBeTruthy();
    }
    const engagement = rows.find((row) => within(row).queryByText("Engagement"))!;
    expect(within(engagement).getByText(/CTR unavailable/)).toBeTruthy();
  });

  it("retains an explicitly selected account filter in the performance link", () => {
    const accountId = "11111111-1111-4111-8111-111111111111";
    render(
      <PartnerPerformance
        data={dashboard()}
        compact
        performanceHref={`/performance?account_id=${accountId}&days=3`}
      />,
    );
    const url = new URL(
      screen.getByRole("link", { name: "View performance" }).getAttribute("href")!,
      "https://partner.example",
    );
    expect(url.pathname).toBe("/performance");
    expect(url.searchParams.get("account_id")).toBe(accountId);
    expect(url.searchParams.get("days")).toBe("3");
  });
});
