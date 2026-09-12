// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FormField } from "@/components/molecules/form-field";
import { imagePreviewUrl } from "@/lib/image-preview";
import { formPayload, initialValues } from "@/lib/form-values";
import { resources, type FieldSpec } from "@/lib/resources";

const logo = "https://images.example/logo.svg";
const cover = "https://images.example/cover.png";
const advance = (ms = 300) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
function Fixture({
  type = "image-url",
  initial = cover,
  disabled = false,
}: {
  type?: FieldSpec["type"];
  initial?: string;
  disabled?: boolean;
}) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <form aria-label="Details">
      <FormField
        field={{
          key: "asset",
          label: "Asset URL",
          type,
          required: true,
          max: 2048,
          help: "Public image URL",
        }}
        value={value}
        onChange={setValue}
        disabled={disabled}
      />
    </form>
  );
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("URL-derived fields", () => {
  it.each(["url", "logo-url", "image-url"] as const)(
    "preserves native URL validation, form data, and field metadata for %s",
    (type) => {
      render(<Fixture type={type} />);
      const input = screen.getByRole("textbox", { name: "Asset URL" }) as HTMLInputElement;
      expect(input.type).toBe("url");
      expect(input.required).toBe(true);
      expect(input.maxLength).toBe(2048);
      expect(input.getAttribute("aria-describedby")).toBe("asset-help");
      expect(new FormData(screen.getByRole("form") as HTMLFormElement).get("asset")).toBe(cover);
      fireEvent.change(input, { target: { value: "not a URL" } });
      expect(input.validity.typeMismatch).toBe(true);
      fireEvent.change(input, { target: { value: "" } });
      expect(input.validity.valueMissing).toBe(true);
    },
  );

  it("renders a compact logo inside the field and debounces changed URLs without showing a stale logo", async () => {
    render(<Fixture type="logo-url" initial={logo} />);
    const oldImage = screen.getByAltText("Logo preview");
    expect(oldImage.getAttribute("src")).toBe(logo);
    expect(oldImage.getAttribute("referrerpolicy")).toBe("no-referrer");
    fireEvent.load(oldImage);
    expect(screen.queryByRole("status")).toBeNull();
    const input = screen.getByRole("textbox", { name: "Asset URL" });
    fireEvent.change(input, { target: { value: cover } });
    expect(screen.queryByAltText("Logo preview")).toBeNull();
    await advance(200);
    fireEvent.change(input, { target: { value: logo + "?new" } });
    await advance(299);
    expect(screen.queryByAltText("Logo preview")).toBeNull();
    await advance(1);
    const current = screen.getByAltText("Logo preview");
    expect(current.getAttribute("src")).toBe(logo + "?new");
    fireEvent.error(oldImage);
    fireEvent.load(current);
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.queryByAltText("Logo preview")).toBeNull();
  });

  it("shows a non-blocking fallback when a logo fails and resets it for the next URL", async () => {
    render(<Fixture type="logo-url" initial={logo} />);
    fireEvent.error(screen.getByAltText("Logo preview"));
    expect(screen.getByRole("status", { name: "Preview unavailable" })).toBeTruthy();
    expect(
      (screen.getByRole("textbox", { name: "Asset URL" }) as HTMLInputElement).checkValidity(),
    ).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Asset URL" }), {
      target: { value: cover },
    });
    await advance();
    expect(screen.getByAltText("Logo preview").getAttribute("src")).toBe(cover);
  });

  it("loads the large image only after hovering, keeps it open across the gap, and closes on leaving", async () => {
    render(<Fixture />);
    expect(screen.queryByAltText("Image preview")).toBeNull();
    fireEvent.pointerEnter(screen.getByRole("textbox", { name: "Asset URL" }).parentElement!, {
      pointerType: "mouse",
    });
    await advance(249);
    expect(screen.queryByRole("dialog")).toBeNull();
    await advance(1);
    const popup = screen.getByRole("dialog", { name: "Image preview" });
    expect(screen.getByAltText("Image preview").getAttribute("src")).toBe(cover);
    fireEvent.pointerLeave(screen.getByRole("textbox", { name: "Asset URL" }).parentElement!, {
      pointerType: "mouse",
    });
    await advance(100);
    fireEvent.pointerEnter(popup, { pointerType: "mouse" });
    await advance(300);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.pointerLeave(popup, { pointerType: "mouse" });
    await advance(200);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not open on input focus and supports explicit button activation without stealing input focus on hover", async () => {
    render(<Fixture />);
    const input = screen.getByRole("textbox", { name: "Asset URL" });
    input.focus();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.pointerEnter(input.parentElement!);
    await advance();
    expect(document.activeElement).toBe(input);
    fireEvent.click(screen.getByRole("button", { name: "Close image preview" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview image" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.pointerLeave(input.parentElement!);
    await advance();
    expect(screen.getByRole("dialog")).toBeTruthy();
    screen.getByRole("button", { name: "Close image preview" }).focus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Preview image" }));
  });

  it("does not open after a brief hover or an edit and shows failed images without changing the URL", async () => {
    render(<Fixture />);
    const input = screen.getByRole("textbox", { name: "Asset URL" }) as HTMLInputElement;
    fireEvent.pointerEnter(input.parentElement!);
    await advance(100);
    fireEvent.pointerLeave(input.parentElement!);
    await advance();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Preview image" }));
    fireEvent.error(screen.getByAltText("Image preview"));
    expect(screen.getByRole("status", { name: "Preview unavailable" })).toBeTruthy();
    expect(input.value).toBe(cover);
    fireEvent.change(input, { target: { value: logo } });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Preview image" }));
    expect(screen.getByAltText("Image preview").getAttribute("src")).toBe(logo);
  });

  it.each([
    { initial: "" },
    { initial: "broken" },
    { initial: "data:image/svg+xml,bad" },
    { disabled: true },
  ])("does not open invalid or disabled previews: %j", async (props) => {
    render(<Fixture {...props} />);
    expect(
      (screen.getByRole("button", { name: "Preview image" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.pointerEnter(screen.getByRole("textbox", { name: "Asset URL" }).parentElement!);
    await advance();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("uses preview fields throughout resource forms without changing API payloads", () => {
    for (const resource of ["sources", "topics", "articles"] as const) {
      const fields = resources[resource].fields.filter((field) =>
        ["image_url", "logo_url"].includes(field.key),
      );
      expect(fields.length).toBeGreaterThan(0);
      const values = initialValues(resource);
      for (const field of fields) {
        expect(field.type).toBe(field.key === "logo_url" ? "logo-url" : "image-url");
        expect(formPayload(resource, values)[field.key]).toBeNull();
        values[field.key] = cover;
        expect(formPayload(resource, values)[field.key]).toBe(cover);
      }
    }
  });
});

describe("preview URL eligibility", () => {
  it.each([
    "",
    null,
    "not a url",
    "/logo.png",
    "javascript:alert(1)",
    "data:image/png;base64,x",
    "file:///logo.png",
    "ftp://images.example/logo.png",
    "https://user:secret@images.example/a",
    "https://images.example/white space",
    "https://images.example/\u0000a",
    "https://images.example/" + "x".repeat(2048),
  ])("does not request %j", (value) => {
    expect(imagePreviewUrl(value)).toBeNull();
  });
  it("supports remote HTTP(S), query strings, and Unicode URLs without altering the saved value", () => {
    expect(imagePreviewUrl(cover + "?width=100&token=abc")).toBe(cover + "?width=100&token=abc");
    expect(imagePreviewUrl("http://images.example/日本語.png")).toBe(
      "http://images.example/%E6%97%A5%E6%9C%AC%E8%AA%9E.png",
    );
  });
});
