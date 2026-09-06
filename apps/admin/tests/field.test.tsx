// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Field, type FieldProps } from "@/components/molecules/field";
import { FormField } from "@/components/molecules/form-field";
import { Input } from "@/components/atoms/input";
import { FieldDescription, FieldError, FieldLabel } from "@/components/atoms/field";
import type { FieldSpec } from "@/lib/resources";
import Link from "next/link";

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });
const advance = (ms = 400) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
function Example(props: Omit<FieldProps, "label" | "children">) {
  return <Field label="Title" {...props}>{control => <Input {...control} defaultValue="Developer news" />}</Field>;
}
function descriptions(input: HTMLElement) {
  return (input.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean).map(id => document.getElementById(id));
}

describe("shared field composition", () => {
  it("generates stable, unique control IDs and associates labels without phantom help or error references", () => {
    const { rerender } = render(<><Example /><Example /></>);
    const ids = screen.getAllByRole("textbox", { name: "Title" }).map(input => input.id);
    expect(new Set(ids).size).toBe(2);
    expect(ids.every(Boolean)).toBe(true);
    for (const input of screen.getAllByRole("textbox")) {
      expect((input as HTMLInputElement).labels?.[0].htmlFor).toBe(input.id);
      expect(input.hasAttribute("aria-describedby")).toBe(false);
      expect(input.getAttribute("aria-invalid")).toBe("false");
    }
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<><Example /><Example /></>);
    expect(screen.getAllByRole("textbox").map(input => input.id)).toEqual(ids);
  });

  it("connects subtext, tooltip information, and live validation errors, removing resolved errors", () => {
    const { rerender } = render(<Example id="title" subtext="Use the original title." tooltip="Do not replace this with an AI headline." error="Title is too long." />);
    const input = screen.getByRole("textbox", { name: "Title" });
    expect(descriptions(input).map(element => element?.textContent)).toEqual(["Use the original title.", "Do not replace this with an AI headline.", "Title is too long."]);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").id).toBe("title-error");
    expect(screen.queryByRole("tooltip")).toBeNull();
    rerender(<Example id="title" subtext="Use the original title." />);
    expect(input.getAttribute("aria-describedby")).toBe("title-help");
    expect(input.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.getElementById("title-tooltip")).toBeNull();
    expect((input as HTMLInputElement).value).toBe("Developer news");
  });

  it("preserves explicit IDs, names, native required/disabled behavior and form values", () => {
    const { rerender } = render(<form aria-label="Article"><Example id="article-title" name="title" required /></form>);
    const input = screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement;
    expect(input.id).toBe("article-title");
    expect(input.required).toBe(true);
    expect(input.labels?.[0].querySelector('[aria-hidden="true"]')?.textContent).toBe("*");
    expect(new FormData(screen.getByRole("form") as HTMLFormElement).get("title")).toBe("Developer news");
    fireEvent.change(input, { target: { value: "" } });
    expect(input.validity.valueMissing).toBe(true);
    rerender(<form aria-label="Article"><Example id="article-title" name="title" required disabled /></form>);
    expect(input.disabled).toBe(true);
    expect(input.checkValidity()).toBe(true);
    expect(new FormData(screen.getByRole("form") as HTMLFormElement).has("title")).toBe(false);
  });

  it("supports rich subtext and does not render empty optional content", () => {
    const { rerender } = render(<Example subtext={<>Read the <Link href="/sources">source details</Link>.</>} />);
    expect(screen.getByRole("link", { name: "source details" }).getAttribute("href")).toBe("/sources");
    expect(descriptions(screen.getByRole("textbox"))[0]?.textContent).toBe("Read the source details.");
    rerender(<Example subtext="" error="" tooltip="" />);
    expect(screen.getByRole("textbox").hasAttribute("aria-describedby")).toBe(false);
    expect(document.querySelector('[data-slot="field-description"]')).toBeNull();
    expect(document.querySelector('[data-slot="field-error"]')).toBeNull();
  });

  it("exposes reusable label, subtext and error atoms with caller styling", () => {
    render(<><FieldLabel htmlFor="test" required>Label</FieldLabel><FieldDescription className="custom">Helpful text</FieldDescription><FieldError>Error text</FieldError><FieldError /></>);
    expect(screen.getByText("Label").getAttribute("for")).toBe("test");
    expect(screen.getByText("Helpful text").className).toContain("custom");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it.each(["text", "number", "datetime", "textarea", "lines", "boolean", "url", "logo-url", "image-url", "select", "language", "reference"] as const)("applies the same accessible wrapper to %s controls", type => {
    const field: FieldSpec = { key: "detail", label: "Detail", type, required: true, help: "Required metadata", choices: ["article"], resource: "topics" };
    render(<FormField field={field} value={type === "boolean" ? false : ""} onChange={vi.fn()} error="Please check this field." disabled />);
    const role = type === "boolean" ? "checkbox" : ["select", "language", "reference"].includes(type) ? "combobox" : type === "number" ? "spinbutton" : null;
    const control = role ? screen.getByRole(role, { name: "Detail" }) : document.getElementById("detail")!;
    expect(control.getAttribute("aria-invalid")).toBe("true");
    expect(descriptions(control).map(element => element?.textContent)).toEqual(["Required metadata", "Please check this field."]);
    expect(control.matches(":disabled")).toBe(true);
    expect(document.querySelectorAll('[data-slot="field"]')).toHaveLength(1);
  });
});

describe("field help tooltip", () => {
  it("opens on keyboard focus and dismisses with Escape without moving focus", async () => {
    render(<Example tooltip="Extra context" />);
    const help = screen.getByRole("button", { name: "About Title" });
    await act(async () => help.focus());
    expect(screen.getByRole("tooltip").textContent).toBe("Extra context");
    fireEvent.keyDown(help, { key: "Escape" });
    await advance();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.activeElement).toBe(help);
  });

  it("opens after a hover delay and never opens the associated combobox", async () => {
    render(<FormField field={{ key: "type", label: "Type", type: "select", choices: ["publisher"], tooltip: "Extra context" }} value="publisher" onChange={vi.fn()} />);
    fireEvent.pointerMove(screen.getByRole("button", { name: "About Type" }), { pointerType: "mouse" });
    await advance(349);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await advance(1);
    expect(screen.getByRole("tooltip").textContent).toBe("Extra context");
    expect(screen.getByRole("combobox").getAttribute("aria-expanded")).toBe("false");
  });

  it("toggles on click or tap without submitting the form or changing the value", async () => {
    const submit = vi.fn(event => event.preventDefault());
    render(<form onSubmit={submit}><Example tooltip="Extra context" /></form>);
    const help = screen.getByRole("button", { name: "About Title" });
    fireEvent.pointerDown(help, { pointerType: "touch" });
    fireEvent.click(help);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    await advance();
    fireEvent.pointerDown(help, { pointerType: "touch" });
    fireEvent.click(help);
    await advance();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(submit).not.toHaveBeenCalled();
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Developer news");
  });
});
