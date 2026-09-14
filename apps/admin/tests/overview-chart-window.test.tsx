// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { useChartWindow } from "@/components/organisms/overview-chart-window";
afterEach(cleanup);
it("focuses sparse history without changing totals or filling missing values", () => {
  const source = Array.from({ length: 30 }, (_, i) => ({
    date: `2026-09-${String(i + 1).padStart(2, "0")}`,
    value: i === 28 ? 5 : i === 29 ? null : 0,
  }));
  function Chart() {
    const window = useChartWindow(source, (row) => (row.value ?? 0) > 0);
    return (
      <>
        {window.control}
        <output>{JSON.stringify(window.rows)}</output>
      </>
    );
  }
  render(<Chart />);
  expect(JSON.parse(screen.getByRole("status").textContent!).length).toBe(7);
  fireEvent.click(screen.getByRole("button", { name: "Show full period" }));
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual(source);
  expect(source[29].value).toBeNull();
});
