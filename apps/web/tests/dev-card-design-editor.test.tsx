// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DevCardDesignEditor } from "@/components/dev-card-design-editor";
import { DevCardContentEditor } from "@/components/dev-card-content-editor";

afterEach(cleanup);

it("keeps supplementary motion help behind an info button", async () => {
  const onChange = vi.fn();
  render(<DevCardDesignEditor value={undefined} onChange={onChange} />);
  expect(screen.queryByText("Bring your card to life.")).toBeNull();
  expect(screen.queryByText("The original colorful mosaic")).toBeNull();
  expect(screen.queryByRole("tooltip")).toBeNull();
  const help = screen.getByRole("button", { name: "About card motion" });
  expect(help.getAttribute("type")).toBe("button");
  fireEvent.click(help);
  expect((await screen.findByRole("tooltip")).textContent).toContain("Image downloads stay static");
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(help);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("uses labeled radio groups and preserves card content when changing design", () => {
  const onChange = vi.fn();
  render(
    <DevCardDesignEditor
      value={{ stats: ["current_streak"], technologies: ["python"] }}
      onChange={onChange}
    />,
  );
  expect(screen.getAllByRole("radiogroup")).toHaveLength(3);
  expect(screen.getByRole("radio", { name: "Animated" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("radio", { name: "Aurora" }));
  expect(onChange).toHaveBeenLastCalledWith({
    theme: "aurora",
    stats: ["current_streak"],
    technologies: ["python"],
  });
  fireEvent.click(screen.getByRole("radio", { name: "Blue" }));
  expect(onChange).toHaveBeenLastCalledWith({
    accent: "blue",
    stats: ["current_streak"],
    technologies: ["python"],
  });
});

it("keeps the saved design when toggling a reading stat through its label", () => {
  const onChange = vi.fn();
  render(
    <DevCardContentEditor
      stack={[]}
      value={{ theme: "terminal", accent: "rose", motion: "static", stats: ["current_streak"] }}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByText("Current reading streak"));
  expect(onChange).toHaveBeenLastCalledWith({
    theme: "terminal",
    accent: "rose",
    motion: "static",
    stats: [],
    technologies: null,
  });
  expect(screen.getByText("Add technologies to your stack.")).toBeTruthy();
});
