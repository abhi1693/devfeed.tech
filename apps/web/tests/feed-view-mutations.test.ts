// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
import { userRequest } from "@/lib/user";
import { interestsChanged, saveFeedView, validFeedViewReceipt } from "@/lib/user-mutations";

vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
beforeEach(() => vi.mocked(userRequest).mockReset());

it("saves only the selected feed layout with CSRF without refreshing article interests", async () => {
  vi.mocked(userRequest).mockResolvedValue({ view: "stacked" });
  const changed = vi.fn();
  window.addEventListener(interestsChanged, changed);
  try {
    expect(await saveFeedView("latest", "stacked", { csrfToken: "csrf" })).toEqual({
      view: "stacked",
    });
    expect(userRequest).toHaveBeenCalledWith("settings/feed-view?feed=latest", {
      method: "PUT",
      body: JSON.stringify({ view: "stacked" }),
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
    });
    expect(changed).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener(interestsChanged, changed);
  }
});

it.each([
  null,
  [],
  { view: "future" },
  { view: "cards", extra: true },
  { view: { toString: (): string => "cards" } },
])("rejects malformed layout acknowledgement %j", async (receipt) => {
  expect(validFeedViewReceipt(receipt)).toBe(false);
  vi.mocked(userRequest).mockResolvedValue(receipt);
  await expect(saveFeedView("latest", "stacked", { csrfToken: "csrf" })).rejects.toThrow(
    "Invalid mutation acknowledgement",
  );
});

it("rejects Columns outside My feed before a write and rejects incompatible receipts", async () => {
  expect(() => saveFeedView("latest", "columns", { csrfToken: "csrf" })).toThrow(
    "Unsupported feed layout",
  );
  expect(userRequest).not.toHaveBeenCalled();
  vi.mocked(userRequest).mockResolvedValue({ view: "columns" });
  await expect(saveFeedView("latest", "stacked", { csrfToken: "csrf" })).rejects.toThrow(
    "Invalid mutation acknowledgement",
  );
  expect(await saveFeedView("my_feed", "columns", { csrfToken: "csrf" })).toEqual({
    view: "columns",
  });
});
