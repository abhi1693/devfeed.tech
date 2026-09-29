// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CopyButton } from "@/components/copy-button";

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(navigator, "clipboard");
  Reflect.deleteProperty(document, "execCommand");
});
it.each([false, true])(
  "copies multiline setup prompts with the fallback (denied=%s)",
  async (denied) => {
    if (denied)
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: vi.fn().mockRejectedValue(new Error("Denied")) },
      });
    const text = "Connect my agent.\nUse https://example.test/mcp";
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => {
        const field = document.activeElement as HTMLTextAreaElement;
        expect(field.value).toBe(text);
        expect(field.selectionEnd).toBe(text.length);
        return true;
      }),
    });
    const status = vi.fn();
    render(<CopyButton text={text} label="Copy setup prompt" onStatusChange={status} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy setup prompt" }));
    await screen.findByRole("button", { name: "Copy setup prompt" });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(status).toHaveBeenLastCalledWith("copied"));
    expect(document.querySelector("textarea")).toBeNull();
  },
);
