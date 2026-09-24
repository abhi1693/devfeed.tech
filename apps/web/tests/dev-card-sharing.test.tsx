// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DevCardSharing } from "@/components/dev-card-sharing";
import type { UserProfile } from "@/lib/user";
import { extensionEvent } from "@/lib/extension-analytics";

vi.mock("@/lib/reader-runtime", () => ({
  readerPublicOrigin: () => "https://devfeed.tech",
  readerWebsiteLink: (path: string) => ({
    href: `https://devfeed.tech${path}`,
    target: "_blank",
    rel: "noopener noreferrer",
  }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const profile: UserProfile = {
  display_name: "Reader",
  avatar_url: null,
  username: "reader",
  visibility: { public: true, stack: true, heatmap: true, location: true, achievements: false },
};
it.each([
  { ...profile, visibility: { ...profile.visibility!, public: false } },
  { ...profile, username: null },
])("does not offer public links without a saved public username", (value) => {
  render(<DevCardSharing profile={value} unsaved={false} />);
  expect(screen.queryByRole("button", { name: "Copy Link" })).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});
it("does not share unsaved profile changes", () => {
  render(<DevCardSharing profile={profile} unsaved />);
  expect(screen.queryByRole("button")).toBeNull();
});
it("copies profile links and Markdown embeds from extension settings", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  render(<DevCardSharing profile={profile} unsaved={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
  await screen.findByText("Link copied.");
  expect(writeText).toHaveBeenLastCalledWith("https://devfeed.tech/users/reader");
  fireEvent.click(screen.getByRole("button", { name: "Copy Markdown" }));
  await screen.findByText("Markdown copied.");
  expect(writeText).toHaveBeenLastCalledWith(
    "[![DevFeed card](https://devfeed.tech/api/v1/users/reader/card.svg)](https://devfeed.tech/users/reader)",
  );
  expect(screen.getByRole("link", { name: "View public profile" }).getAttribute("target")).toBe(
    "_blank",
  );
});
it("keeps a usable public link when clipboard access fails", async () => {
  vi.stubGlobal("navigator", {
    clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
  });
  render(<DevCardSharing profile={profile} unsaved={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy Link" }));
  await screen.findByText(
    "Couldn’t copy. Select the embed code or open your public profile to copy its address.",
  );
  expect(screen.getByRole("link", { name: "View public profile" })).toBeTruthy();
});
it("allows coarse sharing events but rejects personal analytics parameters", () => {
  expect(extensionEvent({ name: "dev_card_share", params: { method: "link" } })).toBeTruthy();
  expect(
    extensionEvent({ name: "dev_card_share", params: { method: "link", username: "reader" } }),
  ).toBeNull();
  expect(extensionEvent({ name: "dev_card_view", params: {} })).toBeTruthy();
});
