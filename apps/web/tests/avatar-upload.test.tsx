// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserAccount, UserProvider } from "@/components/user-account";
import { ProfileSettings } from "@/components/profile-settings";
import type { UserProfile } from "@/lib/user";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const variants = ([32, 64, 128, 256, 512] as const).map((width) => ({
  width,
  url: `https://images.example.test/avatars/reader/${width}.webp?v=new`,
}));
const uploaded = {
  display_name: "Reader",
  username: "reader",
  avatar_url: variants[3].url,
  avatar_variants: variants,
};

function setup() {
  let profile: UserProfile = {
    ...uploaded,
    avatar_url: "https://example.com/old.png",
    avatar_variants: [],
  };
  let fail = false;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/me"))
      return Response.json({ user_id: "reader", name: "Reader", csrf_token: "csrf" });
    if (url.endsWith("/avatar")) {
      if (fail) return new Response(null, { status: 503 });
      profile =
        init?.method === "DELETE"
          ? { ...uploaded, avatar_url: null, avatar_variants: [] }
          : uploaded;
      return Response.json(profile);
    }
    if (init?.method === "PUT") {
      profile = { ...profile, ...JSON.parse(String(init.body)) };
    }
    return Response.json(profile);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <UserProvider>
      <UserAccount />
      <ProfileSettings />
    </UserProvider>,
  );
  return {
    fetcher,
    fail: (value: boolean) => {
      fail = value;
    },
  };
}

function selectFile(file = new File(["picture"], "photo.png", { type: "image/png" })) {
  fireEvent.change(screen.getByLabelText("Avatar image"), { target: { files: [file] } });
}

it("uploads with CSRF, refreshes avatar sizes and retains unsaved profile edits", async () => {
  const { fetcher } = setup();
  const bio = await screen.findByRole("textbox", { name: "Short bio" });
  fireEvent.change(bio, { target: { value: "Unsaved bio" } });
  selectFile();
  await screen.findByText("Avatar updated.");
  expect(bio).toHaveProperty("value", "Unsaved bio");
  const upload = fetcher.mock.calls.find(([url]) => url.endsWith("/avatar"))!;
  expect(upload[1]?.headers).toEqual({ "X-CSRF-Token": "csrf" });
  expect(upload[1]?.body).toBeInstanceOf(FormData);
  expect((upload[1]?.body as FormData).get("file")).toBeInstanceOf(File);
  expect(screen.getByRole("textbox", { name: "Avatar URL" })).toHaveProperty(
    "value",
    uploaded.avatar_url,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Your profile is saved.");
  const saved = JSON.parse(
    String(fetcher.mock.calls.find(([, init]) => init?.method === "PUT")![1]?.body),
  );
  expect(saved.bio).toBe("Unsaved bio");
  expect(saved).not.toHaveProperty("avatar_url");
  expect(saved).not.toHaveProperty("avatar_variants");
});

it("preserves the old avatar on failure, retries, and keeps removal after discarding other edits", async () => {
  const setupResult = setup();
  const field = await screen.findByRole("textbox", { name: "Avatar URL" });
  setupResult.fail(true);
  selectFile();
  await screen.findByText("Couldn’t update your avatar. Try again.");
  expect(field).toHaveProperty("value", "https://example.com/old.png");
  setupResult.fail(false);
  selectFile();
  await screen.findByText("Avatar updated.");
  fireEvent.change(screen.getByRole("textbox", { name: "Short bio" }), {
    target: { value: "Draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await screen.findByText("Avatar removed.");
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(field).toHaveProperty("value", "");
  expect(screen.getByRole("textbox", { name: "Short bio" })).toHaveProperty("value", "");
  expect(
    setupResult.fetcher.mock.calls.find(([, init]) => init?.method === "DELETE")![1]?.headers,
  ).toEqual({ "X-CSRF-Token": "csrf" });
});

it("rejects invalid types and oversized files before an upload request", async () => {
  const { fetcher } = setup();
  await screen.findByRole("textbox", { name: "Avatar URL" });
  selectFile(new File(["<svg/>"], "avatar.svg", { type: "image/svg+xml" }));
  await screen.findByText("Choose a JPEG, PNG or WebP image.");
  selectFile(new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }));
  await screen.findByText("Choose an image under 5 MB.");
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/avatar"))).toHaveLength(0);
});

it("disables saving while an avatar request is pending", async () => {
  const { fetcher } = setup();
  const bio = await screen.findByRole("textbox", { name: "Short bio" });
  fireEvent.change(bio, { target: { value: "Draft" } });
  let finish!: (value: Response) => void;
  fetcher.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  selectFile();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty("disabled", true),
  );
  await act(async () => finish(Response.json(uploaded)));
  await screen.findByText("Avatar updated.");
});
