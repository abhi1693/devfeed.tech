// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { PartnerPerformance } from "../src/app/partner-performance";
import { performanceCsv, performanceDays } from "../src/lib/performance";
import type { WorkspaceDashboard } from "../src/lib/api/generated/models";

afterEach(cleanup);

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

  it("lets readers switch the trend metric without leaving the report", async () => {
    render(<PartnerPerformance data={dashboard()} />);
    expect(screen.getByRole("figure", { name: "Impressions" })).toBeTruthy();
    const selector = screen.getByRole("group", { name: "Trend metric" });
    await userEvent.click(within(selector).getByRole("button", { name: "Clicks" }));
    expect(screen.getByRole("figure", { name: "Clicks" })).toBeTruthy();
    expect(
      within(selector).getByRole("button", { name: "Clicks" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.queryByRole("dialog")).toBeNull();
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
    expect(screen.getByRole("figure", { name: "Impressions" })).toBeTruthy();
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

  it("keeps the compact dashboard focused and links to the user-wide report and same period", () => {
    render(<PartnerPerformance data={dashboard({ end: "2026-10-07" })} compact />);
    expect(screen.getByRole("link", { name: "View performance" }).getAttribute("href")).toBe(
      "/performance?days=7",
    );
    expect(screen.queryByRole("region", { name: "Format comparison" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Export daily CSV" })).toBeNull();
  });

  it("retains an explicitly selected account filter in the performance link", () => {
    const accountId = "11111111-1111-4111-8111-111111111111";
    render(<PartnerPerformance data={dashboard()} compact accountId={accountId} />);
    const url = new URL(
      screen.getByRole("link", { name: "View performance" }).getAttribute("href")!,
      "https://partner.example",
    );
    expect(url.pathname).toBe("/performance");
    expect(url.searchParams.get("account_id")).toBe(accountId);
    expect(url.searchParams.get("days")).toBe("3");
  });
});
