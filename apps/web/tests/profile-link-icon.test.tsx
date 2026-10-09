// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ProfileLinkIcon } from "@/components/profile-link-icon";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses only local hashed brand assets without remote requests or visible labels", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const { container, rerender } = render(<ProfileLinkIcon url="https://bitbucket.org/reader" />);
  expect(screen.getByRole("img", { name: "Bitbucket" })).toBeTruthy();
  const bitbucketPath =
    container.querySelector<HTMLElement>(".profile-brand-mark")?.style.maskImage;
  expect(bitbucketPath).toMatch(/\/profile-icons\/bitbucket\.[a-f0-9]{12}\.svg/);
  expect(container.textContent).toBe("");
  rerender(<ProfileLinkIcon url="https://gitlab.com/reader" />);
  expect(screen.getByRole("img", { name: "GitLab" })).toBeTruthy();
  expect(container.querySelector<HTMLElement>(".profile-brand-mark")?.style.maskImage).not.toBe(
    bitbucketPath,
  );
  rerender(<ProfileLinkIcon url="https://github.com/reader" />);
  expect(container.querySelector("path")?.getAttribute("d")).toBeTruthy();
  expect(container.querySelector(".profile-brand-mark")).toBeNull();
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
