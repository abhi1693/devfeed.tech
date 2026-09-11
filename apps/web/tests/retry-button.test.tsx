// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RetryButton } from "@devfeed/ui/retry-button";
afterEach(cleanup);
it("retries without submitting a surrounding form and blocks repeated attempts while pending", () => {
  const retry = vi.fn(), submit = vi.fn();
  const view = render(<form onSubmit={submit}><RetryButton onRetry={retry} /></form>);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(submit).not.toHaveBeenCalled();
  view.rerender(<form onSubmit={submit}><RetryButton onRetry={retry} pending /></form>);
  const button = screen.getByRole("button", { name: "Trying again…" });
  expect(button.getAttribute("aria-busy")).toBe("true");
  fireEvent.click(button);
  expect(retry).toHaveBeenCalledOnce();
});
it("preserves native navigation for server-render retries", () => {
  render(<RetryButton href="" />);
  expect(screen.getByText("Try again", { selector: "a" }).getAttribute("href")).toBe("");
});
