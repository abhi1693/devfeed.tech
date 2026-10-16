// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CollapsibleSidebar } from "@devfeed/ui/sidebar";

const key = "test:sidebar-expanded";
function sidebar(storageKey = key, variant: "reader" | "workspace" = "workspace") {
  return (
    <CollapsibleSidebar
      id="test-sidebar"
      label="Test navigation"
      storageKey={storageKey}
      variant={variant}
      syncDocumentState={variant === "reader"}
    >
      <nav aria-label="Test links">
        <a href="#products" className="nav-item" aria-label="Products">
          <svg aria-hidden />
          <span>Products</span>
        </a>
      </nav>
    </CollapsibleSidebar>
  );
}
beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.sidebarState;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("starts as the reader's icon rail and remembers expansion across remounts", () => {
  const view = render(sidebar());
  const nav = screen.getByRole("complementary", { name: "Test navigation" });
  expect(nav.getAttribute("data-expanded")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(nav.getAttribute("data-expanded")).toBe("true");
  expect(localStorage.getItem(key)).toBe("true");
  expect(
    screen.getByRole("button", { name: "Collapse sidebar" }).getAttribute("aria-controls"),
  ).toBe("test-sidebar");
  view.unmount();
  render(sidebar());
  expect(
    screen.getByRole("button", { name: "Collapse sidebar" }).getAttribute("aria-pressed"),
  ).toBe("true");
});

it("synchronizes the existing reader event and cross-tab storage updates", () => {
  render(sidebar(key, "reader"));
  expect(document.documentElement.dataset.sidebarState).toBe("collapsed");
  expect(screen.queryByRole("button", { name: "Navigation" })).toBeNull();
  act(() => {
    localStorage.setItem(key, "true");
    window.dispatchEvent(new Event("devfeed:sidebar-state-change"));
  });
  expect(document.documentElement.dataset.sidebarState).toBe("expanded");
  act(() => {
    localStorage.setItem(key, "false");
    window.dispatchEvent(new StorageEvent("storage", { key }));
  });
  expect(document.documentElement.dataset.sidebarState).toBe("collapsed");
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
});

it("keeps application preferences separate and ignores unrelated storage changes", () => {
  render(
    <>
      {sidebar("test:partner-sidebar")}
      {sidebar("test:admin-sidebar")}
    </>,
  );
  const sidebars = screen.getAllByRole("complementary", { name: "Test navigation" });
  fireEvent.click(within(sidebars[0]).getByRole("button", { name: "Expand sidebar" }));
  expect(sidebars[0].getAttribute("data-expanded")).toBe("true");
  expect(sidebars[1].getAttribute("data-expanded")).toBe("false");
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: "unrelated" })));
  expect(sidebars[0].getAttribute("data-expanded")).toBe("true");
  expect(document.documentElement.dataset.sidebarState).toBeUndefined();
});

it.each(["read", "write"])("keeps the toggle usable when storage %s is blocked", (operation) => {
  const deniedKey = `test:sidebar-${operation}-blocked`;
  vi.spyOn(Storage.prototype, operation === "read" ? "getItem" : "setItem").mockImplementation(
    () => {
      throw new DOMException("Blocked", "SecurityError");
    },
  );
  render(sidebar(deniedKey));
  fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
});

it("opens mobile labels independently of the desktop preference and closes on links or Escape", () => {
  render(sidebar());
  const toggle = screen.getByRole("button", { name: "Navigation" });
  const nav = screen.getByRole("complementary", { name: "Test navigation" });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(nav.getAttribute("data-mobile-open")).toBe("true");
  expect(nav.getAttribute("data-expanded")).toBe("false");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(toggle);
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("link", { name: "Products" }));
  expect(nav.getAttribute("data-mobile-open")).toBe("false");
  expect(localStorage.getItem(key)).toBeNull();
});
