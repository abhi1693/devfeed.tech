// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { userRequest } from "@/lib/user";
import { interestsChanged, saveTagPreferences, setTagFollow } from "@/lib/user-mutations";

vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
const changed = vi.fn();
const options = { csrfToken: "test-csrf" };
beforeEach(() => {
  vi.mocked(userRequest).mockReset();
  changed.mockClear();
  window.addEventListener(interestsChanged, changed);
});
afterEach(() => window.removeEventListener(interestsChanged, changed));

it.each([true, false])(
  "acknowledges tag follow=%s and refreshes reading interests",
  async (followed) => {
    vi.mocked(userRequest).mockResolvedValue({ followed });
    expect(await setTagFollow("tag/with punctuation", followed, options)).toEqual({ followed });
    expect(userRequest).toHaveBeenCalledWith("preferences/tags/tag%2Fwith%20punctuation", {
      method: "PUT",
      body: JSON.stringify({ followed }),
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "test-csrf" },
    });
    expect(changed).toHaveBeenCalledTimes(1);
  },
);

it.each([{ ids: [] }, { ids: ["tag-a", "tag-b"] }])(
  "saves a validated tag selection: $ids",
  async ({ ids }) => {
    vi.mocked(userRequest).mockResolvedValue({ tag_ids: ids });
    expect(await saveTagPreferences(ids, options)).toEqual({ tag_ids: ids });
    expect(changed).toHaveBeenCalledTimes(1);
  },
);

it.each([null, [], { followed: "true" }])(
  "rejects malformed follow receipts: %j",
  async (receipt) => {
    vi.mocked(userRequest).mockResolvedValue(receipt);
    await expect(setTagFollow("tag-a", true, options)).rejects.toThrow(
      "Invalid mutation acknowledgement",
    );
    expect(changed).not.toHaveBeenCalled();
  },
);

it.each([null, { tag_ids: "tag-a" }, { tag_ids: [null] }])(
  "rejects malformed selection receipts: %j",
  async (receipt) => {
    vi.mocked(userRequest).mockResolvedValue(receipt);
    await expect(saveTagPreferences(["tag-a"], options)).rejects.toThrow(
      "Invalid mutation acknowledgement",
    );
    expect(changed).not.toHaveBeenCalled();
  },
);
