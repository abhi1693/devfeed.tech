// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useProfileEditor } from "@/lib/use-profile-editor";
import { readDevCardDraft, saveDevCardDraft } from "@/lib/dev-card-draft";
import { AccountError, type UserProfile, type UserStack } from "@/lib/user";

const initial: UserProfile = { display_name: null, avatar_url: null };
const stack: UserStack = {
  topic_id: "python",
  name: "Python",
  slug: "python",
  kind: "language",
  section: "primary",
  since_year: null,
  logo_url: null,
  status: "active",
};
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

it("normalizes defaults without replacing local edits when the saved profile refreshes", () => {
  const save = vi.fn();
  const { result, rerender } = renderHook(
    ({ profile }) => useProfileEditor(profile, "Provider name", save),
    { initialProps: { profile: initial } },
  );
  expect(result.current.value.display_name).toBe("Provider name");
  expect(result.current.value.links).toEqual([]);
  expect(result.current.dirty).toBe(false);
  act(() => result.current.change({ ...result.current.value, bio: "Unsaved" }));
  rerender({ profile: { ...initial, bio: "Remote" } });
  expect(result.current.value.bio).toBe("Unsaved");
  act(() => result.current.discard());
  expect(result.current.value.bio).toBeNull();
  expect(result.current.dirty).toBe(false);
});

it("restores a ready card draft and clears it when saved or discarded", async () => {
  saveDevCardDraft({ name: "Draft reader", stack: [stack], ready: true, created: Date.now() });
  const save = vi.fn(async (value: UserProfile) => ({ ...value, username: "claimed" }));
  const { result, unmount } = renderHook(() => useProfileEditor(initial, "Provider", save));
  expect(result.current.value.display_name).toBe("Draft reader");
  expect(result.current.value.stack).toEqual([stack]);
  await act(() => result.current.save());
  expect(result.current.baseline.username).toBe("claimed");
  expect(result.current.dirty).toBe(false);
  expect(result.current.message).toBe("Your profile is saved.");
  expect(readDevCardDraft()).toBeNull();
  unmount();
  saveDevCardDraft({ name: "Another draft", stack: [], ready: true, created: Date.now() });
  const discarded = renderHook(() => useProfileEditor(initial, "Provider", save));
  act(() => discarded.result.current.discard());
  expect(discarded.result.current.value.display_name).toBe("Provider");
  expect(readDevCardDraft()).toBeNull();
});

it.each([409, 422, 500])(
  "retains edits and clears feedback after a failed save (%s)",
  async (status) => {
    const save = vi.fn().mockRejectedValue(new AccountError(status));
    const { result } = renderHook(() => useProfileEditor(initial, "Provider", save));
    act(() => result.current.change({ ...result.current.value, bio: "Keep me" }));
    await act(() => result.current.save());
    expect(result.current.error).toBe(true);
    expect(result.current.busy).toBe(false);
    expect(result.current.dirty).toBe(true);
    expect(result.current.value.bio).toBe("Keep me");
    expect(result.current.message).toMatch(
      status === 409 ? /username/ : status === 422 ? /fields/ : /try again/,
    );
    act(() => result.current.change({ ...result.current.value, bio: "Try this" }));
    expect(result.current.message).toBe("");
    expect(result.current.error).toBe(false);
  },
);

it("removes deleted and past technologies from explicit card selections", () => {
  const { result } = renderHook(() =>
    useProfileEditor(
      { ...initial, stack: [stack], dev_card: { stats: [], technologies: ["python", "missing"] } },
      "Provider",
      vi.fn(),
    ),
  );
  act(() => result.current.changeStack([{ ...stack, section: "past" }]));
  expect(result.current.value.dev_card?.technologies).toEqual([]);
  act(() => result.current.discard());
  expect(result.current.value.dev_card?.technologies).toEqual(["python", "missing"]);
});
