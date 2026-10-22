// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SettingsToastProvider, useSettingsToast } from "@/components/settings-toast";

function Controls() {
  const notify = useSettingsToast();
  return (
    <>
      <button onClick={() => notify("Appearance saved.")}>Save appearance</button>
      <button onClick={() => notify("Couldn’t save appearance.", "error")}>Fail save</button>
    </>
  );
}

function app() {
  return render(
    <SettingsToastProvider>
      <Controls />
    </SettingsToastProvider>,
  );
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function advance(ms: number) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

it("does not announce or display a notification before a save", () => {
  app();
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("listitem")).toBeNull();
  expect(screen.queryByRole("button", { name: "Dismiss notification" })).toBeNull();
});

it("politely announces a successful save and supports dismissal", async () => {
  app();
  fireEvent.click(screen.getByRole("button", { name: "Save appearance" }));
  await advance(50);
  expect(within(screen.getByRole("listitem")).getByText("Appearance saved.")).toBeTruthy();
  const announcement = screen.getByRole("status");
  expect(announcement.getAttribute("aria-live")).toBe("polite");
  expect(announcement.textContent).toContain("Appearance saved.");
  expect(announcement.textContent).not.toContain("Dismiss");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
  expect(screen.queryByRole("listitem")).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

it("announces a save failure with higher priority", async () => {
  app();
  fireEvent.click(screen.getByRole("button", { name: "Fail save" }));
  await advance(50);
  const announcement = screen.getByRole("status");
  expect(announcement.getAttribute("aria-live")).toBe("assertive");
  expect(announcement.textContent).toContain("Couldn’t save appearance.");
  expect(screen.getByRole("listitem").getAttribute("data-variant")).toBe("error");
});

it("announces repeated saves again and gives the latest save a full display duration", async () => {
  app();
  fireEvent.click(screen.getByRole("button", { name: "Save appearance" }));
  await advance(4000);
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save appearance" }));
  await advance(50);
  expect(screen.getByRole("status").textContent).toContain("Appearance saved.");
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
  await advance(3950);
  expect(screen.getByRole("listitem")).toBeTruthy();
  await advance(1001);
  expect(screen.queryByRole("listitem")).toBeNull();
});
