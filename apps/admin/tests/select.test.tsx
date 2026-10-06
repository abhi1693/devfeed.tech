// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Select, type SelectOption, type SelectProps } from "@/components/molecules/select";

const options = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
  { value: "g", label: "Gamma" },
];
function Example({
  required = false,
  disabled = false,
  initial = "b",
  save = () => {},
  choices = options,
  search,
}: {
  required?: boolean;
  disabled?: boolean;
  initial?: string;
  save?: (data: FormData) => void;
  choices?: SelectOption[];
  search?: SelectProps["search"];
}) {
  const [value, setValue] = useState(initial);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save(new FormData(event.currentTarget));
      }}
    >
      <Select
        label="Choice"
        name="choice"
        options={choices}
        value={value}
        onChange={setValue}
        required={required}
        disabled={disabled}
        search={search}
      />
      <button type="submit">Save</button>
    </form>
  );
}
afterEach(cleanup);

it("opens a styled list without search, focuses the selected option, and restores focus on selection", async () => {
  const user = userEvent.setup();
  render(<Example />);
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger);
  expect(screen.queryByRole("textbox")).toBeNull();
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Beta" })),
  );
  await user.keyboard("{ArrowDown}{Enter}");
  expect(trigger.textContent).toBe("Gamma");
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await user.keyboard("{ArrowDown}{Home}{Enter}");
  expect(trigger.textContent).toBe("Select…");
});

it("supports typeahead, Space and Escape without submitting the form", async () => {
  const save = vi.fn();
  const user = userEvent.setup();
  render(<Example save={save} required />);
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger);
  await user.keyboard("ga ");
  expect(trigger.textContent).toBe("Gamma");
  expect(save).not.toHaveBeenCalled();
  await user.click(trigger);
  await user.keyboard("{Home}{Escape}");
  expect(trigger.textContent).toBe("Gamma");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shares required validation and FormData with Combobox", async () => {
  const save = vi.fn();
  const user = userEvent.setup();
  render(<Example initial="" required save={save} />);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole("combobox", { name: "Choice" }).getAttribute("aria-invalid")).toBe(
    "true",
  );
  await user.click(screen.getByRole("option", { name: "Alpha" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save.mock.lastCall?.[0].get("choice")).toBe("a");
});

it("does not open when disabled by the field or its form fieldset", async () => {
  const user = userEvent.setup();
  const view = render(<Example disabled />);
  await user.click(screen.getByRole("combobox", { name: "Choice" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  view.rerender(
    <fieldset disabled>
      <Example />
    </fieldset>,
  );
  await user.click(screen.getByRole("combobox", { name: "Choice" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps full option information and selection feedback when the selected label is shorter", async () => {
  const choices = [
    {
      value: "recommended",
      label: "Recommended articles from the sources and topics you follow",
      description: "Includes the reading interests saved to your account.",
      meta: "Personalized",
    },
    { value: "most_liked", label: "Most liked" },
  ];
  const save = vi.fn();
  const user = userEvent.setup();
  render(<Example required initial="most_liked" choices={choices} save={save} />);
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger);
  const recommended = screen.getByRole("option", {
    name: `${choices[0].label} ${choices[0].description} ${choices[0].meta}`,
  });
  const selected = screen.getByRole("option", { name: "Most liked" });
  expect(recommended.textContent).toBe(
    `${choices[0].label}${choices[0].description}${choices[0].meta}`,
  );
  expect(selected.querySelector("[data-checked]")?.getAttribute("data-checked")).toBe("true");
  expect(recommended.querySelector("[data-checked]")?.getAttribute("data-checked")).toBe("false");

  await user.keyboard("{Home}{Enter}");
  expect(trigger.textContent).toBe(choices[0].label);
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await user.click(trigger);
  expect(
    screen
      .getByRole("option", { name: /Recommended articles/ })
      .querySelector("[data-checked]")
      ?.getAttribute("data-checked"),
  ).toBe("true");
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save.mock.lastCall?.[0].get("choice")).toBe("recommended");
});

it("searches and selects long Unicode and URL options without losing their submitted value", async () => {
  const source = {
    value: "long-source",
    label: "開発者ニュース" + "技術情報".repeat(20),
    description: "https://publisher.example/" + "very-long-unbroken-path-".repeat(15),
    meta: "ja-JP",
    keywords: ["rss-feed"],
  };
  const save = vi.fn();
  const user = userEvent.setup();
  render(
    <Example
      required
      initial="short-source"
      choices={[{ value: "short-source", label: "Docs" }, source]}
      search={{ placeholder: "Find sources" }}
      save={save}
    />,
  );
  const trigger = screen.getByRole("combobox", { name: "Choice" });
  await user.click(trigger);
  const search = screen.getByPlaceholderText("Find sources");
  expect(document.activeElement).toBe(search);
  await user.type(search, "rss-feed");
  expect(screen.queryByRole("option", { name: "Docs" })).toBeNull();
  expect(
    screen.getByRole("option", { name: `${source.label} ${source.description} ${source.meta}` })
      .textContent,
  ).toBe(`${source.label}${source.description}${source.meta}`);
  await user.keyboard("{ArrowDown}{Enter}");
  expect(trigger.textContent).toBe(source.label);
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(save.mock.lastCall?.[0].get("choice")).toBe(source.value);
});
