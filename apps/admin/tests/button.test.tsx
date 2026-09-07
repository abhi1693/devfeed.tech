// @vitest-environment jsdom
import { createRef } from "react";
import Link from "next/link";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Button } from "@/components/atoms/button";

afterEach(cleanup);

describe("shared button", () => {
  it("does not submit forms unless explicitly configured as a submit button", () => {
    const submit = vi.fn(event => event.preventDefault());
    render(<form onSubmit={submit}><Button>Preview</Button><Button type="submit">Save</Button></form>);
    const preview = screen.getByRole("button", { name: "Preview" }) as HTMLButtonElement;
    expect(preview.type).toBe("button");
    fireEvent.click(preview);
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it.each(["default", "outline", "secondary", "ghost", "destructive", "destructive-ghost", "link"] as const)("shares cursor, focus and reduced-motion behavior for %s", variant => {
    render(<Button variant={variant}>Action</Button>);
    const button = screen.getByRole("button");
    expect(button.getAttribute("data-variant")).toBe(variant);
    expect(button.className).toContain("cursor-pointer");
    expect(button.className).toContain("focus-visible:ring");
    expect(button.className).toContain("motion-reduce:transition-none");
    expect(button.className).toContain("not-disabled:not-aria-disabled:hover:");
    expect(button.className).not.toContain("disabled:pointer-events-none");
  });

  it("shows loading feedback, prevents repeat activation and restores the original state", () => {
    const click = vi.fn();
    const { rerender } = render(<Button onClick={click} loading loadingText="Saving…">Save</Button>);
    const button = screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.querySelector('[data-slot="button-spinner"]')?.getAttribute("aria-hidden")).toBe("true");
    expect(button.hasAttribute("loadingtext")).toBe(false);
    fireEvent.click(button);
    expect(click).not.toHaveBeenCalled();
    rerender(<Button onClick={click}>Save</Button>);
    expect(button.disabled).toBe(false);
    expect(button.hasAttribute("aria-busy")).toBe(false);
    expect(button.querySelector('[data-slot="button-spinner"]')).toBeNull();
    fireEvent.click(button);
    expect(click).toHaveBeenCalledTimes(1);
  });

  it.each([{ disabled: true }, { "aria-disabled": true }, { "aria-disabled": "true" as const }])("blocks disabled button handlers: %j", props => {
    const click = vi.fn(), capture = vi.fn();
    render(<Button {...props} onClick={click} onClickCapture={capture}>Delete</Button>);
    const button = screen.getByRole("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(click).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
  });

  it("respects inherited disabled fieldsets", () => {
    const click = vi.fn();
    render(<fieldset disabled><Button onClick={click}>Save</Button></fieldset>);
    const button = screen.getByRole("button");
    expect(button.matches(":disabled")).toBe(true);
    fireEvent.click(button);
    expect(click).not.toHaveBeenCalled();
  });

  it("forwards refs and accessibility props and allows deliberate size overrides", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref} size="icon-xs" className="size-4" aria-label="Show help" aria-expanded={false} aria-controls="details"><span aria-hidden>?</span></Button>);
    const button = screen.getByRole("button", { name: "Show help" });
    expect(ref.current).toBe(button);
    expect(button.getAttribute("aria-controls")).toBe("details");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.className.split(" ")).toContain("size-4");
    expect(button.className.split(" ")).not.toContain("size-6");
  });

  it("preserves Next.js link semantics with asChild and adds no nested button", () => {
    render(<Button asChild variant="outline"><Link href="/content/sources" prefetch={false}>Sources</Link></Button>);
    const link = screen.getByRole("link", { name: "Sources" });
    expect(link.getAttribute("href")).toBe("/content/sources");
    expect(link.getAttribute("type")).toBeNull();
    expect(link.getAttribute("data-slot")).toBe("button");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it.each([{ disabled: true }, { loading: true }, { "aria-disabled": true }])("blocks normal child link activation when unavailable: %j", props => {
    const childClick = vi.fn(event => event.preventDefault()), parentClick = vi.fn();
    render(<Button asChild {...props} onClick={parentClick}><a href="https://example.test/" onClick={childClick}>Open</a></Button>);
    const link = screen.getByRole("link", { name: "Open" });
    expect(link.getAttribute("aria-disabled")).toBe("true");
    expect(link.getAttribute("tabindex")).toBe("-1");
    expect(fireEvent.click(link)).toBe(false);
    expect(fireEvent.keyDown(link, { key: "Enter" })).toBe(false);
    expect(fireEvent(link, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }))).toBe(false);
    expect(childClick).not.toHaveBeenCalled();
    expect(parentClick).not.toHaveBeenCalled();
    if ("loading" in props) {
      expect(link.getAttribute("aria-busy")).toBe("true");
      expect(link.querySelector('[data-slot="button-spinner"]')).toBeTruthy();
    }
  });

  it("composes enabled child handlers and retains icon-only accessible names while loading", () => {
    const childClick = vi.fn(event => event.preventDefault()), parentClick = vi.fn();
    const { rerender } = render(<Button asChild onClick={parentClick}><a href="https://example.test/" onClick={childClick}>Open</a></Button>);
    fireEvent.click(screen.getByRole("link"));
    expect(childClick).toHaveBeenCalledTimes(1);
    expect(parentClick).toHaveBeenCalledTimes(1);
    rerender(<Button size="icon" loading aria-label="Refresh" />);
    expect(screen.getByRole("button", { name: "Refresh" }).getAttribute("aria-busy")).toBe("true");
  });
});


it.each(["icon", "icon-xs", "icon-sm", "icon-lg"] as const)("replaces the %s action icon with one spinner without changing its label", size => {
  const { rerender } = render(<Button size={size} aria-label="Run AI analysis"><svg data-testid="action-icon" /></Button>);
  const button = screen.getByRole("button", { name: "Run AI analysis" });
  const classes = button.className;
  rerender(<Button size={size} loading aria-label="Run AI analysis"><svg data-testid="action-icon" /></Button>);
  expect(button.querySelectorAll("svg")).toHaveLength(1);
  expect(button.querySelector('[data-slot="button-spinner"]')).toBeTruthy();
  expect(button.querySelector('[data-testid="action-icon"]')).toBeNull();
  expect(button.className).toBe(classes);
  rerender(<Button size={size} aria-label="Run AI analysis"><svg data-testid="action-icon" /></Button>);
  expect(button.querySelector('[data-testid="action-icon"]')).toBeTruthy();
});
