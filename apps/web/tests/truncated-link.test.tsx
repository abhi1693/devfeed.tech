// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TruncatedLink } from "@/components/truncated-link";

afterEach(cleanup);

function setup(truncated: boolean) {
  const user = userEvent.setup();
  const title = "Same mission, bigger stage: OpenAI hires founders to help Codex grow";
  const view = render(
    <TruncatedLink href="/articles/example" prefetch={false}>
      {title}
    </TruncatedLink>,
  );
  const link = view.getByRole("link", { name: title });
  Object.defineProperties(link, {
    clientHeight: { value: 54, configurable: true },
    scrollHeight: { value: truncated ? 81 : 54, configurable: true },
  });
  return { user, view, link, title };
}

it("shows the full clipped title on keyboard focus and dismisses it with Escape", async () => {
  const { user, view, link, title } = setup(true);
  await user.tab();
  expect(document.activeElement).toBe(link);
  expect((await view.findByRole("tooltip")).textContent).toBe(title);
  expect(link.getAttribute("href")).toBe("/articles/example");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(view.queryByRole("tooltip")).toBeNull());
});

it("shows the full clipped title on hover outside the card's clipping container", async () => {
  const { user, view, link, title } = setup(true);
  await user.hover(link);
  const tooltip = await view.findByRole("tooltip");
  expect(tooltip.textContent).toBe(title);
  expect(view.container.contains(tooltip)).toBe(false);
  await user.unhover(link);
  fireEvent.pointerMove(document.body, { clientX: 1000, clientY: 1000, pointerType: "mouse" });
  await waitFor(() => expect(view.queryByRole("tooltip")).toBeNull());
});

it("does not show a tooltip for a title that fits, and rechecks after layout changes", async () => {
  const { user, view, link } = setup(false);
  await user.tab();
  expect(view.queryByRole("tooltip")).toBeNull();
  await user.tab();
  Object.defineProperty(link, "scrollHeight", { value: 81 });
  await user.tab({ shift: true });
  expect(await view.findByRole("tooltip")).toBeTruthy();
});
