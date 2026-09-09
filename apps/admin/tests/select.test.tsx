// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Select } from "@/components/molecules/select";

const options = [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }, { value: "g", label: "Gamma" }];
function Example({ required = false, disabled = false, initial = "b", save = () => {} }: { required?: boolean; disabled?: boolean; initial?: string; save?: (data: FormData) => void }) {
  const [value, setValue] = useState(initial);
  return <form onSubmit={event => { event.preventDefault(); save(new FormData(event.currentTarget)); }}>
    <Select label="Choice" name="choice" options={options} value={value} onChange={setValue} required={required} disabled={disabled} />
    <button type="submit">Save</button>
  </form>;
}
afterEach(cleanup);

it("opens a styled list without search, focuses the selected option, and restores focus on selection", async () => {
  const user = userEvent.setup(); render(<Example />);
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger);
  expect(screen.queryByRole("textbox")).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("option", { name: "Beta" })));
  await user.keyboard("{ArrowDown}{Enter}");
  expect(trigger.textContent).toBe("Gamma");
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await user.keyboard("{ArrowDown}{Home}{Enter}");
  expect(trigger.textContent).toBe("Select…");
});

it("supports typeahead, Space and Escape without submitting the form", async () => {
  const save = vi.fn(); const user = userEvent.setup(); render(<Example save={save} required />);
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger); await user.keyboard("ga ");
  expect(trigger.textContent).toBe("Gamma");
  expect(save).not.toHaveBeenCalled();
  await user.click(trigger); await user.keyboard("{Home}{Escape}");
  expect(trigger.textContent).toBe("Gamma");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shares required validation and FormData with Combobox", async () => {
  const save = vi.fn(); const user = userEvent.setup(); render(<Example initial="" required save={save} />);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole("combobox", { name: "Choice" }).getAttribute("aria-invalid")).toBe("true");
  await user.click(screen.getByRole("option", { name: "Alpha" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save.mock.lastCall?.[0].get("choice")).toBe("a");
});

it("does not open when disabled by the field or its form fieldset", async () => {
  const user = userEvent.setup(); const view = render(<Example disabled />);
  await user.click(screen.getByRole("combobox", { name: "Choice" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  view.rerender(<fieldset disabled><Example /></fieldset>);
  await user.click(screen.getByRole("combobox", { name: "Choice" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
