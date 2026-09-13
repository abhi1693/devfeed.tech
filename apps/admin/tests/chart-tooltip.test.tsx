// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ChartTooltip, DistributionTooltip } from "@/components/atoms/chart";

afterEach(cleanup);

it("separates the donut label, count and share without repeating the center label", () => {
  render(
    <DistributionTooltip
      active
      total={2561}
      payload={[{ graphicalItemId: "test-series", name: "Actionable", value: 2490 }]}
    />,
  );
  const tooltip = within(screen.getByRole("status"));
  expect(tooltip.getByText("Actionable").tagName).toBe("P");
  expect(tooltip.getByText("2,490").tagName).toBe("STRONG");
  expect(tooltip.getByText("97.2%")).toBeTruthy();
});

it("keeps custom count formatting and handles empty or inactive distributions", () => {
  const view = render(
    <DistributionTooltip
      active
      total={0}
      payload={[{ graphicalItemId: "test-series", name: "Tokens", value: 0 }]}
    />,
  );
  expect(screen.getByText("0.0%")).toBeTruthy();
  view.rerender(
    <DistributionTooltip
      active
      total={1000000}
      payload={[{ graphicalItemId: "test-series", name: "Tokens", value: 1000000 }]}
      formatValue={() => "1M"}
    />,
  );
  expect(screen.getByText("1M")).toBeTruthy();
  view.rerender(<DistributionTooltip active={false} total={0} payload={[]} />);
  expect(screen.queryByRole("status")).toBeNull();
});

it("shows long series names and exact values without an empty heading", () => {
  render(
    <ChartTooltip
      active
      payload={[
        {
          graphicalItemId: "test-series",
          dataKey: "tokens",
          name: "A long model name",
          value: 123456789,
        },
      ]}
    />,
  );
  const tooltip = screen.getByRole("status");
  expect(within(tooltip).getByText("A long model name")).toBeTruthy();
  expect(within(tooltip).getByText("123,456,789")).toBeTruthy();
  expect(tooltip.querySelector("p")).toBeNull();
});
