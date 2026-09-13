// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Calendar, DatePicker } from "@devfeed/ui/date-picker";

afterEach(cleanup);
it("supports leap days and keyboard navigation across months", async () => {
  const change = vi.fn();
  render(<Calendar value="2024-02-29" onChange={change} />);
  const leap = screen.getByRole("button", { name: "Thursday, February 29, 2024" });
  leap.focus();
  fireEvent.keyDown(leap, { key: "ArrowRight" });
  const next = screen.getByRole("button", { name: "Friday, March 1, 2024" });
  await waitFor(() => expect(document.activeElement).toBe(next));
  fireEvent.click(next);
  expect(change).toHaveBeenCalledWith("2024-03-01");
  fireEvent.keyDown(next, { key: "PageUp" });
  await waitFor(() => expect(document.activeElement?.getAttribute("data-date")).toBe("2024-02-01"));
});
it("disables out-of-range days and clamps keyboard navigation", async () => {
  render(<Calendar value="2026-09-10" min="2026-09-10" max="2026-09-15" onChange={vi.fn()} />);
  expect(
    (screen.getByRole("button", { name: "Previous month" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Wednesday, September 9, 2026" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  const selected = screen.getByRole("button", { name: "Thursday, September 10, 2026" });
  selected.focus();
  fireEvent.keyDown(selected, { key: "ArrowLeft" });
  expect(document.activeElement).toBe(selected);
});
it("uses shared month/year dropdowns to jump to historical dates", () => {
  render(<Calendar value="2026-09-10" onChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Year" }));
  fireEvent.click(screen.getByRole("option", { name: "2024" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Month" }));
  fireEvent.click(screen.getByRole("option", { name: "February" }));
  expect(screen.getByRole("grid", { name: "February 2024" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Thursday, February 29, 2024" })).toBeTruthy();
});
function Form() {
  const [value, setValue] = useState("2026-09-10");
  return (
    <form aria-label="Dates">
      <DatePicker name="date" label="From date" value={value} onChange={setValue} />
    </form>
  );
}
it("selects and clears ISO dates through FormData and restores focus", async () => {
  render(<Form />);
  const trigger = screen.getByRole("button", { name: "From date" });
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Saturday, September 12, 2026" }));
  expect(
    new FormData(screen.getByRole("form", { name: "Dates" }) as HTMLFormElement).get("date"),
  ).toBe("2026-09-12");
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Clear date" }));
  expect(
    new FormData(screen.getByRole("form", { name: "Dates" }) as HTMLFormElement).get("date"),
  ).toBe("");
});
it("Escape dismisses the calendar without changing the selected value", async () => {
  const change = vi.fn();
  render(<DatePicker label="Date" value="2026-09-10" onChange={change} />);
  const trigger = screen.getByRole("button", { name: "Date" });
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Date" }), { key: "Escape" });
  await act(async () => {});
  expect(screen.queryByRole("dialog", { name: "Date" })).toBeNull();
  expect(change).not.toHaveBeenCalled();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});
it("disabled date pickers do not open or submit values", () => {
  render(
    <form aria-label="Dates">
      <DatePicker label="Date" name="date" value="2026-09-10" onChange={vi.fn()} disabled />
    </form>,
  );
  const trigger = screen.getByRole("button", { name: "Date" });
  expect((trigger as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(trigger);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    new FormData(screen.getByRole("form", { name: "Dates" }) as HTMLFormElement).has("date"),
  ).toBe(false);
});
