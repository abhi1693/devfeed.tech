// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DevCardSharing } from "@/components/dev-card-sharing";
import { DevCardPreview } from "@/components/dev-card-preview";
import type { UserIdentity, UserProfile } from "@/lib/user";
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
const user: UserIdentity = {
  user_id: "reader",
  name: "Reader",
  email: "reader@example.test",
  csrf_token: "test",
  expires_at: 9999999999,
};
it.each([
  { ...profile, visibility: { ...profile.visibility!, public: false } },
  { ...profile, username: null },
])("does not offer public links without a saved public username", (value) => {
  render(<DevCardPreview profile={value} user={user} unsaved={false} />);
  expect(screen.queryByRole("button", { name: "Copy Link" })).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});
it("does not share unsaved profile changes", () => {
  render(<DevCardSharing profile={profile} unsaved />);
  expect(screen.queryByRole("button")).toBeNull();
});
it("places the public profile beside downloads and copies Markdown from extension settings", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  render(<DevCardPreview profile={profile} user={user} unsaved={false} />);
  expect(screen.queryByRole("button", { name: "Copy Link" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Copy Markdown" })).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "Embed" }));
  expect(screen.queryByRole("button", { name: "Download card" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Copy Markdown" }));
  await screen.findByText("Markdown copied.");
  expect(writeText).toHaveBeenLastCalledWith(
    "[![DevFeed card](https://devfeed.tech/api/v1/users/reader/card.svg)](https://devfeed.tech/users/reader)",
  );
  fireEvent.click(screen.getByRole("radio", { name: "Dev Card" }));
  expect(screen.getByRole("link", { name: "View public profile" }).getAttribute("target")).toBe(
    "_blank",
  );
  const link = screen.getByRole("link", { name: "View public profile" });
  expect(link.parentElement).toBe(
    screen.getByRole("button", { name: "Download card" }).parentElement,
  );
  fireEvent.click(screen.getByRole("radio", { name: "Header" }));
  expect(screen.getByRole("link", { name: "View public profile" }).parentElement).toBe(
    screen.getByRole("button", { name: "Download X header" }).parentElement,
  );
});
it("keeps a usable public link when clipboard access fails", async () => {
  vi.stubGlobal("navigator", {
    clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
  });
  render(<DevCardPreview profile={profile} user={user} unsaved={false} />);
  fireEvent.click(screen.getByRole("radio", { name: "Embed" }));
  fireEvent.click(screen.getByRole("button", { name: "Copy Markdown" }));
  await screen.findByText("Couldn’t copy. Select the embed code and copy it manually.");
  fireEvent.click(screen.getByRole("radio", { name: "Dev Card" }));
  expect(screen.getByRole("link", { name: "View public profile" })).toBeTruthy();
});
it("allows coarse sharing events but rejects personal analytics parameters", () => {
  expect(extensionEvent({ name: "dev_card_share", params: { method: "link" } })).toBeTruthy();
  expect(
    extensionEvent({ name: "dev_card_share", params: { method: "link", username: "reader" } }),
  ).toBeNull();
  expect(extensionEvent({ name: "dev_card_view", params: {} })).toBeTruthy();
});
