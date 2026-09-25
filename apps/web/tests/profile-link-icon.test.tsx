// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ProfileLinkIcon } from "@/components/profile-link-icon";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("updates bundled brand icons in place without requests or visible labels", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { container, rerender } = render(<ProfileLinkIcon url="https://github.com/reader" />);
  expect(screen.getByRole("img", { name: "GitHub" })).toBeTruthy();
  const githubPath = container.querySelector("path")?.getAttribute("d");
  expect(githubPath).toBeTruthy();
  expect(container.textContent).toBe("");
  rerender(<ProfileLinkIcon url="https://gitlab.com/reader" />);
  expect(screen.getByRole("img", { name: "GitLab" })).toBeTruthy();
  expect(container.querySelector("path")?.getAttribute("d")).not.toBe(githubPath);
  rerender(<ProfileLinkIcon url="https://linkedin.com/in/reader" />);
  expect(screen.getByRole("img", { name: "LinkedIn" })).toBeTruthy();
  expect(container.querySelector("img")).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it("uses a globe for websites and a link icon for empty or invalid inputs", () => {
  const { container, rerender } = render(
    <ProfileLinkIcon url="https://github.com.example.org/reader" />,
  );
  expect(screen.getByRole("img", { name: "Website" })).toBeTruthy();
  expect(container.querySelector("svg.lucide-globe")).toBeTruthy();
  rerender(<ProfileLinkIcon url="" />);
  expect(screen.getByRole("img", { name: "Link" })).toBeTruthy();
  expect(container.querySelector("svg.lucide-link-2")).toBeTruthy();
  rerender(<ProfileLinkIcon url="javascript:alert(1)" />);
  expect(screen.getByRole("img", { name: "Link" })).toBeTruthy();
});
